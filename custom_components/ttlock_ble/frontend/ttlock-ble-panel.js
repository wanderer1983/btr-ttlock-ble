// TTLock sidebar panel for the ttlock_ble integration.
//
// One card per lock: its state, when a proxy last heard it, and the
// fingerprints, cards and passcodes it holds, with add, rename and delete.
// Anything that needs a session first makes sure the lock is awake: a
// padlock is silent until touched, so the panel asks for the touch and
// waits for the lock's next advertisement before it starts.
//
// Plain custom element, no build step: Home Assistant serves this file
// as it is and sets `hass` and `narrow` on the element.

const DOMAIN = "ttlock_ble";
const POLL_MS = 3000;
// A lock heard this recently is awake enough to start a session at once.
const AWAKE_NOW_S = 5;
const WAKE_WAIT_S = 120;

const TYPES = {
  fingerprint: {
    title: "Fingerprints",
    one: "fingerprint",
    add: "add_fingerprint",
    del: "delete_fingerprint",
    attr: "fingerprint",
  },
  card: { title: "Cards", one: "card", add: "add_card", del: "delete_card", attr: "card" },
  passcode: { title: "Passcodes", one: "passcode" },
};

const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );

const errorText = (err) =>
  (err && (err.message || err.error?.message || (typeof err === "string" ? err : ""))) ||
  "Unknown error";

function ago(seconds) {
  if (seconds < 60) return `${Math.round(seconds)} s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} d ago`;
}

class TtlockBlePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._locks = null;
    this._loadError = null;
    this._jobs = {}; // device_id -> job
    this._fetchedAt = 0;
    this._dialog = null;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this._renderShell();
      this._start();
    }
    const menu = this.shadowRoot.querySelector("ha-menu-button");
    if (menu) menu.hass = hass;
  }

  get hass() {
    return this._hass;
  }

  set narrow(narrow) {
    this._narrow = narrow;
    const menu = this.shadowRoot.querySelector("ha-menu-button");
    if (menu) menu.narrow = narrow;
  }

  connectedCallback() {
    if (this._hass && !this._timer) this._start();
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._timer = null;
    if (this._unsubEvents) {
      this._unsubEvents.then((unsub) => unsub()).catch(() => {});
      this._unsubEvents = null;
    }
  }

  _start() {
    this._refresh();
    this._timer = setInterval(() => this._refresh(), POLL_MS);
    if (!this._unsubEvents) {
      this._unsubEvents = this._hass.connection.subscribeEvents(
        (event) => this._onEnrollment(event.data),
        `${DOMAIN}_enrollment`,
      );
    }
  }

  async _refresh() {
    try {
      const result = await this._hass.callWS({ type: `${DOMAIN}/locks` });
      this._locks = result.locks;
      this._fetchedAt = Date.now();
      this._loadError = null;
    } catch (err) {
      this._loadError = errorText(err);
    }
    this._render();
  }

  _heardNow(lock) {
    if (!lock.heard) return null;
    return lock.heard.seconds_ago + (Date.now() - this._fetchedAt) / 1000;
  }

  // ---- jobs ---------------------------------------------------------------

  _setJob(lock, patch) {
    const job = { ...(this._jobs[lock.device_id] || {}), ...patch };
    this._jobs[lock.device_id] = job;
    this._render();
    return job;
  }

  _say(lock, line, kind = "info") {
    const job = this._jobs[lock.device_id];
    if (!job) return;
    job.lines = [...(job.lines || []), { line, kind }];
    this._render();
  }

  // Run `work` once the lock is awake, reporting progress on its card.
  async _onLock(lock, title, wakeHint, work) {
    if (this._jobs[lock.device_id]?.state === "running") return;
    this._setJob(lock, { title, state: "running", lines: [], kind: null });
    try {
      const heard = this._heardNow(lock);
      if (heard === null || heard > AWAKE_NOW_S) {
        this._say(lock, wakeHint, "action");
        const { awake } = await this._hass.callWS({
          type: `${DOMAIN}/wait_awake`,
          device_id: lock.device_id,
          timeout: WAKE_WAIT_S,
        });
        if (!awake) throw new Error(`${lock.name} did not wake within ${WAKE_WAIT_S} s`);
        this._say(lock, "Lock is awake, connecting…");
      } else {
        this._say(lock, "Connecting…");
      }
      const done = await work();
      this._say(lock, done || "Done", "ok");
      this._setJob(lock, { state: "done" });
    } catch (err) {
      this._say(lock, errorText(err), "error");
      this._setJob(lock, { state: "failed" });
    }
    this._refresh();
  }

  _onEnrollment(data) {
    const lock = (this._locks || []).find((l) => l.device_id === data.device_id);
    if (!lock || !this._jobs[lock.device_id]) return;
    const card = data.credential_type === "card";
    switch (data.stage) {
      case "waiting":
        this._say(
          lock,
          card
            ? "Hold the card to the reader"
            : "Place your finger on the sensor (the lock stays silent until it reads it)",
          "action",
        );
        break;
      case "scan_accepted":
        this._say(lock, `Scan ${data.scans} accepted, lift and place the same finger again`, "action");
        break;
      case "enrolled":
        this._say(lock, `Enrolled as ${data.number}`, "ok");
        break;
      case "failed":
        this._say(lock, "The lock ended enrolment", "error");
        break;
      default:
        break;
    }
  }

  _service(service, data, returnResponse = false) {
    return this._hass.callService(DOMAIN, service, data, undefined, false, returnResponse);
  }

  // ---- actions ------------------------------------------------------------

  _unlock(lock) {
    this._onLock(lock, "Unlock", "Touch the lock to wake it", async () => {
      await this._hass.callService("lock", "unlock", { entity_id: lock.entity_id }, undefined, false);
      return "Unlocked";
    });
  }

  _readLists(lock) {
    this._onLock(lock, "Read credentials", "Touch the lock to wake it", async () => {
      await this._hass.callWS({ type: `${DOMAIN}/refresh_credentials`, device_id: lock.device_id });
      return "Lists read from the lock";
    });
  }

  _validity(values) {
    const data = {};
    if (values.start) data.start = values.start;
    if (values.end) data.end = values.end;
    return data;
  }

  _add(lock, type) {
    const spec = TYPES[type];
    if (type === "passcode") {
      this._openDialog({
        title: `Add a passcode to ${lock.name}`,
        fields: [
          { id: "passcode", label: "Code (4 to 9 digits)", type: "password", pattern: "\\d{4,9}", required: true },
          { id: "name", label: "Whose is it (optional)" },
          { id: "start", label: "Valid from (optional)", type: "datetime-local" },
          { id: "end", label: "Valid until (optional)", type: "datetime-local" },
        ],
        submit: "Add",
        onSubmit: (v) =>
          this._onLock(lock, "Add passcode", "Touch the lock to wake it", async () => {
            await this._service("add_passcode", {
              device_id: lock.device_id,
              passcode: v.passcode,
              name: v.name || "",
              ...this._validity(v),
            });
            return "Passcode added";
          }),
      });
      return;
    }
    this._openDialog({
      title: `Add a ${spec.one} to ${lock.name}`,
      note:
        type === "fingerprint"
          ? "After you start, wake the lock, then place the same finger on the sensor several times. The lock waits about a minute."
          : "After you start, wake the lock, then hold the card to the reader. The lock waits about a minute.",
      fields: [
        { id: "name", label: "Whose is it (optional)" },
        { id: "start", label: "Valid from (optional)", type: "datetime-local" },
        { id: "end", label: "Valid until (optional)", type: "datetime-local" },
      ],
      submit: "Start",
      onSubmit: (v) =>
        this._onLock(lock, `Add ${spec.one}`, "Touch the lock to wake it", async () => {
          const result = await this._service(
            spec.add,
            { device_id: lock.device_id, name: v.name || "", ...this._validity(v) },
            true,
          );
          const number = result?.response?.[spec.attr];
          return number !== undefined ? `${spec.one[0].toUpperCase()}${spec.one.slice(1)} ${number} added` : "Added";
        }),
    });
  }

  _rename(lock, type, cred) {
    this._openDialog({
      title: `Name ${TYPES[type].one} ${cred.label}`,
      fields: [{ id: "name", label: "Whose is it", value: cred.name }],
      submit: "Save",
      onSubmit: async (v) => {
        try {
          await this._hass.callWS({
            type: `${DOMAIN}/rename_credential`,
            device_id: lock.device_id,
            credential_type: type,
            key: cred.key,
            name: v.name || "",
          });
        } catch (err) {
          this._setJob(lock, { title: "Rename", state: "failed", lines: [{ line: errorText(err), kind: "error" }] });
        }
        this._refresh();
      },
    });
  }

  _delete(lock, type, cred) {
    const spec = TYPES[type];
    const who = cred.name ? ` (${cred.name})` : "";
    this._openDialog({
      title: `Delete ${spec.one} ${cred.label}${who}?`,
      note: "It stops opening the lock as soon as the lock confirms.",
      fields: [],
      submit: "Delete",
      danger: true,
      onSubmit: () =>
        this._onLock(lock, `Delete ${spec.one}`, "Touch the lock to wake it", async () => {
          if (type === "passcode") {
            await this._hass.callWS({ type: `${DOMAIN}/delete_passcode`, device_id: lock.device_id, key: cred.key });
          } else {
            await this._service(spec.del, { device_id: lock.device_id, [spec.attr]: cred.label });
          }
          return `${spec.one[0].toUpperCase()}${spec.one.slice(1)} ${cred.label} deleted`;
        }),
    });
  }

  _changePasscode(lock, cred) {
    this._openDialog({
      title: `Change passcode ${cred.label}`,
      note: "Only a digest of each code is stored here, so type the current code too. The new code is added before the old one is removed.",
      fields: [
        { id: "passcode", label: "Current code", type: "password", pattern: "\\d{4,9}", required: true },
        { id: "new_passcode", label: "New code", type: "password", pattern: "\\d{4,9}", required: true },
      ],
      submit: "Change",
      onSubmit: (v) =>
        this._onLock(lock, "Change passcode", "Touch the lock to wake it", async () => {
          await this._service("change_passcode", {
            device_id: lock.device_id,
            passcode: v.passcode,
            new_passcode: v.new_passcode,
          });
          return "Passcode changed";
        }),
    });
  }

  _deletePasscodeByDigits(lock) {
    this._openDialog({
      title: `Delete a passcode from ${lock.name} by its digits`,
      note: "For a code the lock does not list, such as one made offline in the app and never used.",
      fields: [{ id: "passcode", label: "Code", type: "password", pattern: "\\d{4,9}", required: true }],
      submit: "Delete",
      danger: true,
      onSubmit: (v) =>
        this._onLock(lock, "Delete passcode", "Touch the lock to wake it", async () => {
          await this._service("delete_passcode", { device_id: lock.device_id, passcode: v.passcode });
          return "Passcode deleted";
        }),
    });
  }

  // ---- dialog -------------------------------------------------------------

  _openDialog(spec) {
    this._dialog = spec;
    const host = this.shadowRoot.getElementById("dialog");
    host.innerHTML = `
      <div class="scrim"></div>
      <form class="dialog" novalidate>
        <h2>${esc(spec.title)}</h2>
        ${spec.note ? `<p class="note">${esc(spec.note)}</p>` : ""}
        ${spec.fields
          .map(
            (f) => `
          <label>${esc(f.label)}
            <input name="${esc(f.id)}" type="${esc(f.type || "text")}" value="${esc(f.value || "")}"
              ${f.pattern ? `pattern="${esc(f.pattern)}" inputmode="numeric" autocomplete="off"` : ""}
              ${f.required ? "required" : ""}>
          </label>`,
          )
          .join("")}
        <p class="form-error" hidden></p>
        <div class="actions">
          <button type="button" class="text" data-cancel>Cancel</button>
          <button type="submit" class="${spec.danger ? "danger" : "primary"}">${esc(spec.submit)}</button>
        </div>
      </form>`;
    host.hidden = false;
    const form = host.querySelector("form");
    const close = () => {
      host.hidden = true;
      host.innerHTML = "";
      this._dialog = null;
    };
    host.querySelector(".scrim").addEventListener("click", close);
    host.querySelector("[data-cancel]").addEventListener("click", close);
    form.addEventListener("keydown", (e) => e.key === "Escape" && close());
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const bad = [...form.querySelectorAll("input")].find((i) => !i.checkValidity());
      if (bad) {
        const error = form.querySelector(".form-error");
        error.textContent = `Check “${bad.closest("label").firstChild.textContent.trim()}”`;
        error.hidden = false;
        bad.focus();
        return;
      }
      const values = Object.fromEntries(new FormData(form).entries());
      if (values.start && values.end && values.end <= values.start) {
        const error = form.querySelector(".form-error");
        error.textContent = "The end must be after the start";
        error.hidden = false;
        return;
      }
      close();
      spec.onSubmit(values);
    });
    const first = form.querySelector("input") || form.querySelector("button[type=submit]");
    first?.focus();
  }

  // ---- rendering ----------------------------------------------------------

  _renderShell() {
    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <div class="toolbar">
        <ha-menu-button></ha-menu-button>
        <div class="title">TTLock</div>
      </div>
      <div class="content" id="content"></div>
      <div id="dialog" hidden></div>`;
    const menu = this.shadowRoot.querySelector("ha-menu-button");
    menu.hass = this._hass;
    menu.narrow = this._narrow;
    this.shadowRoot.getElementById("content").addEventListener("click", (e) => this._onClick(e));
  }

  _onClick(event) {
    const button = event.target.closest("button[data-act]");
    if (!button) return;
    const lock = (this._locks || []).find((l) => l.device_id === button.dataset.lock);
    if (!lock) return;
    const type = button.dataset.type;
    const cred = type ? (lock.credentials[type] || []).find((c) => c.key === button.dataset.key) : null;
    switch (button.dataset.act) {
      case "unlock":
        return this._unlock(lock);
      case "read":
        return this._readLists(lock);
      case "add":
        return this._add(lock, type);
      case "rename":
        return cred && this._rename(lock, type, cred);
      case "delete":
        return cred && this._delete(lock, type, cred);
      case "change":
        return cred && this._changePasscode(lock, cred);
      case "delete-digits":
        return this._deletePasscodeByDigits(lock);
      case "dismiss":
        delete this._jobs[lock.device_id];
        return this._render();
      default:
        return undefined;
    }
  }

  _render() {
    const content = this.shadowRoot.getElementById("content");
    if (!content) return;
    if (this._locks === null) {
      content.innerHTML = this._loadError
        ? `<div class="banner error">${esc(this._loadError)}</div>`
        : `<div class="empty">Loading…</div>`;
      return;
    }
    const banner = this._loadError ? `<div class="banner error">${esc(this._loadError)}</div>` : "";
    content.innerHTML =
      banner +
      (this._locks.length
        ? this._locks.map((lock) => this._renderLock(lock)).join("")
        : `<div class="empty">No TTLock BLE locks are set up.</div>`);
  }

  _renderPresence(lock) {
    const heard = this._heardNow(lock);
    if (heard === null) return `<span class="chip muted">Not heard since Home Assistant started</span>`;
    const via = `via ${esc(lock.heard.via)}, ${esc(lock.heard.rssi)} dBm`;
    if (lock.connected) return `<span class="chip ok">Connected</span>`;
    if (heard <= AWAKE_NOW_S) return `<span class="chip ok" title="${via}">Awake, heard ${ago(heard)}</span>`;
    return `<span class="chip ${heard > 20 ? "muted" : ""}" title="${via}">Asleep, last heard ${ago(heard)}</span>`;
  }

  _renderLock(lock) {
    const busy = this._jobs[lock.device_id]?.state === "running";
    const dis = busy ? "disabled" : "";
    const ids = `data-lock="${esc(lock.device_id)}"`;
    const state =
      lock.locked === true ? "Locked" : lock.locked === false ? "Unlocked" : "State unknown";
    const sub = [lock.lock_name, lock.mac, lock.model].filter(Boolean).map(esc).join(" · ");
    const sections = lock.admin
      ? Object.keys(TYPES)
          .filter((type) => type in lock.credentials)
          .map((type) => this._renderSection(lock, type, dis))
          .join("")
      : `<p class="note">This lock was added with a key that has no admin passcode, so its credentials cannot be managed.</p>`;
    return `
      <section class="lock">
        <header>
          <div>
            <h2>${esc(lock.name)}</h2>
            <div class="sub">${sub}</div>
          </div>
          <div class="chips">
            ${this._renderPresence(lock)}
            <span class="chip">${esc(state)}</span>
            ${lock.battery != null ? `<span class="chip">Battery ${esc(lock.battery)}%</span>` : ""}
          </div>
        </header>
        <div class="row-actions">
          ${lock.entity_id ? `<button class="primary" data-act="unlock" ${ids} ${dis}>Unlock</button>` : ""}
          ${lock.admin ? `<button data-act="read" ${ids} ${dis}>Read from lock</button>` : ""}
        </div>
        ${this._renderJob(lock)}
        ${sections}
      </section>`;
  }

  _renderJob(lock) {
    const job = this._jobs[lock.device_id];
    if (!job) return "";
    const lines = job.lines || [];
    const last = lines.length - 1;
    return `
      <div class="job ${esc(job.state)}">
        <div class="job-head">
          <strong>${esc(job.title)}</strong>
          ${job.state === "running" ? `<span class="spinner" aria-label="Working"></span>` : `<button class="text" data-act="dismiss" data-lock="${esc(lock.device_id)}">Dismiss</button>`}
        </div>
        <ol>
          ${lines
            .map(
              (l, i) =>
                `<li class="${esc(l.kind)} ${i === last && job.state === "running" ? "current" : ""}">${esc(l.line)}</li>`,
            )
            .join("")}
        </ol>
      </div>`;
  }

  _renderSection(lock, type, dis) {
    const spec = TYPES[type];
    const list = lock.credentials[type];
    const ids = `data-lock="${esc(lock.device_id)}" data-type="${esc(type)}"`;
    const rows =
      list === null
        ? `<p class="note">Not read yet. Use “Read from lock”.</p>`
        : list.length === 0
          ? `<p class="note">None.</p>`
          : `<ul>${list
              .map(
                (c) => `
            <li>
              <span class="label">${esc(c.label)}</span>
              <span class="name ${c.name ? "" : "unnamed"}">${esc(c.name || "Unnamed")}</span>
              <span class="item-actions">
                <button class="text" data-act="rename" ${ids} data-key="${esc(c.key)}">Name</button>
                ${type === "passcode" ? `<button class="text" data-act="change" ${ids} data-key="${esc(c.key)}" ${dis}>Change</button>` : ""}
                <button class="text danger" data-act="delete" ${ids} data-key="${esc(c.key)}" ${dis}>Delete</button>
              </span>
            </li>`,
              )
              .join("")}</ul>`;
    return `
      <div class="section">
        <div class="section-head">
          <h3>${spec.title}${list ? ` <span class="count">${list.length}</span>` : ""}</h3>
          <span>
            ${type === "passcode" ? `<button class="text" data-act="delete-digits" ${ids} ${dis}>Delete by digits</button>` : ""}
            <button data-act="add" ${ids} ${dis}>Add</button>
          </span>
        </div>
        ${rows}
      </div>`;
  }
}

const STYLE = `
  :host {
    display: block;
    min-height: 100vh;
    background: var(--primary-background-color);
    color: var(--primary-text-color);
    font-family: var(--ha-font-family-body, Roboto, sans-serif);
  }
  .toolbar {
    display: flex; align-items: center; gap: 4px;
    height: var(--header-height, 56px); padding: 0 12px;
    background: var(--app-header-background-color, var(--primary-color));
    color: var(--app-header-text-color, var(--text-primary-color, #fff));
    border-bottom: var(--app-header-border-bottom, none);
    box-sizing: border-box;
  }
  .toolbar .title { font-size: 20px; margin-left: 12px; }
  .content { max-width: 880px; margin: 0 auto; padding: 16px; display: grid; gap: 16px; }
  .lock {
    background: var(--card-background-color, #fff);
    border-radius: var(--ha-card-border-radius, 12px);
    border: 1px solid var(--divider-color, rgba(0,0,0,.12));
    padding: 16px;
  }
  header { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  h2 { margin: 0; font-size: 20px; font-weight: 500; }
  h3 { margin: 0; font-size: 15px; font-weight: 500; }
  .sub { color: var(--secondary-text-color); font-size: 13px; margin-top: 2px; word-break: break-all; }
  .chips { display: flex; gap: 6px; flex-wrap: wrap; align-items: flex-start; }
  .chip {
    font-size: 12px; padding: 3px 10px; border-radius: 12px;
    background: var(--secondary-background-color, rgba(0,0,0,.06));
  }
  .chip.ok { background: color-mix(in srgb, var(--success-color, #43a047) 18%, transparent); }
  .chip.muted { color: var(--secondary-text-color); }
  .row-actions { display: flex; gap: 8px; margin: 14px 0 4px; flex-wrap: wrap; }
  button {
    font: inherit; font-size: 14px; cursor: pointer; border-radius: 8px;
    padding: 7px 14px; border: 1px solid var(--divider-color, rgba(0,0,0,.2));
    background: transparent; color: var(--primary-color);
  }
  button.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: transparent; }
  button.danger { color: var(--error-color, #db4437); }
  form button.danger { background: var(--error-color, #db4437); color: #fff; border-color: transparent; }
  button.text { border-color: transparent; padding: 6px 8px; }
  button:disabled { opacity: .45; cursor: default; }
  button:focus-visible, input:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
  .section { border-top: 1px solid var(--divider-color, rgba(0,0,0,.12)); margin-top: 14px; padding-top: 10px; }
  .section-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
  .count { color: var(--secondary-text-color); font-weight: 400; margin-left: 4px; }
  ul { list-style: none; margin: 8px 0 0; padding: 0; }
  li { display: flex; align-items: center; gap: 12px; padding: 4px 0; flex-wrap: wrap; }
  .label { font-family: var(--ha-font-family-code, monospace); font-size: 13px; min-width: 9em; }
  .name { flex: 1; min-width: 6em; }
  .unnamed { color: var(--secondary-text-color); font-style: italic; }
  .item-actions { display: flex; gap: 2px; }
  .note { color: var(--secondary-text-color); font-size: 14px; margin: 8px 0; }
  .job {
    margin: 12px 0 4px; padding: 10px 12px; border-radius: 8px;
    background: var(--secondary-background-color, rgba(0,0,0,.05));
    border-left: 4px solid var(--primary-color);
  }
  .job.done { border-left-color: var(--success-color, #43a047); }
  .job.failed { border-left-color: var(--error-color, #db4437); }
  .job-head { display: flex; justify-content: space-between; align-items: center; min-height: 32px; }
  .job ol { margin: 6px 0 0; padding-left: 20px; }
  .job li { display: list-item; padding: 2px 0; color: var(--secondary-text-color); }
  .job li.current, .job li.action.current { color: var(--primary-text-color); font-weight: 500; font-size: 16px; }
  .job li.ok { color: var(--success-color, #43a047); }
  .job li.error { color: var(--error-color, #db4437); }
  .spinner {
    width: 18px; height: 18px; border-radius: 50%;
    border: 2px solid var(--divider-color, rgba(0,0,0,.2)); border-top-color: var(--primary-color);
    animation: spin 0.9s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) { .spinner { animation: none; } }
  .empty, .banner { padding: 24px; text-align: center; color: var(--secondary-text-color); }
  .banner.error { color: var(--error-color, #db4437); padding: 8px; }
  #dialog { position: fixed; inset: 0; z-index: 10; display: grid; place-items: center; padding: 16px; }
  #dialog[hidden] { display: none; }
  .scrim { position: absolute; inset: 0; background: rgba(0,0,0,.45); }
  .dialog {
    position: relative; width: min(440px, 100%); max-height: calc(100vh - 32px); overflow: auto;
    background: var(--card-background-color, #fff); color: var(--primary-text-color);
    border-radius: 16px; padding: 20px; box-sizing: border-box; display: grid; gap: 12px;
  }
  .dialog label { display: grid; gap: 4px; font-size: 13px; color: var(--secondary-text-color); }
  .dialog input {
    font: inherit; font-size: 15px; padding: 9px 10px; border-radius: 8px;
    border: 1px solid var(--divider-color, rgba(0,0,0,.25));
    background: var(--primary-background-color); color: var(--primary-text-color);
  }
  .form-error { color: var(--error-color, #db4437); margin: 0; font-size: 14px; }
  .actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
`;

if (!customElements.get("ttlock-ble-panel")) customElements.define("ttlock-ble-panel", TtlockBlePanel);
