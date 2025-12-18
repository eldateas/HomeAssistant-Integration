// ELDAT Geräte Panel: Custom dialog for device learning
import { LitElement, html, css } from 'lit';

class EldatDevicePanel extends LitElement {
  static styles = css`
    .container { padding: 2em; }
    .title { font-size: 1.5em; margin-bottom: 1em; }
    .status { margin: 1em 0; color: #1976d2; }
    .actions { margin-top: 2em; }
    button { padding: 0.5em 1em; font-size: 1em; }
  `;

  static properties = {
    learning: { type: Boolean },
    status: { type: String },
    deviceType: { type: String },
    deviceData: { type: Object },
    error: { type: String }
  };

  constructor() {
    super();
    this.learning = false;
    this.status = '';
    this.deviceType = '';
    this.deviceData = null;
    this.error = '';
  }

  render() {
    return html`
      <div class="container">
        <div class="title">ELDAT Gerät hinzufügen</div>
        <div>
          <label>Gerätetyp:
            <select @change="${e => this.deviceType = e.target.value}">
              <option value="">Bitte wählen</option>
              <option value="sender">EW-Sender</option>
              <option value="empfaenger">EW-Empfänger</option>
              <option value="neo">EWneo-Sensor</option>
            </select>
          </label>
        </div>
        <div class="actions">
          <button @click="${() => this.startLearning()}" ?disabled="${this.learning || !this.deviceType}">Lernmodus starten</button>
          <button @click="${() => this.cancelLearning()}" ?disabled="${!this.learning}">Abbrechen</button>
        </div>
        <div class="status">${this.status}</div>
        ${this.deviceData ? html`
          <div>
            <b>Gefundenes Gerät:</b>
            <pre>${JSON.stringify(this.deviceData, null, 2)}</pre>
            <button @click="${() => this.saveDevice()}">Speichern</button>
          </div>
        ` : ''}
        ${this.error ? html`<div style="color:red;">${this.error}</div>` : ''}
      </div>
    `;
  }

  async startLearning() {
    this.learning = true;
    this.status = 'Lernmodus läuft...';
    this.error = '';
    this.deviceData = null;
    // TODO: Call backend service to start learning
    // Simulate with timeout
    setTimeout(() => {
      this.learning = false;
      this.status = 'Gerät erkannt!';
      this.deviceData = { id: '123456', type: this.deviceType };
    }, 3000);
  }

  cancelLearning() {
    this.learning = false;
    this.status = 'Lernmodus abgebrochen.';
    this.error = '';
    // TODO: Call backend service to cancel learning
  }

  saveDevice() {
    this.status = 'Gerät gespeichert.';
    // TODO: Call backend service to save device
  }
}

customElements.define('eldat-device-panel', EldatDevicePanel);
