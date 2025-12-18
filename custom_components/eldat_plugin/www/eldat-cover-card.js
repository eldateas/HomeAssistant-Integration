class EldatCoverCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
  }

  setConfig(config) {
    if (!config.entity) {
      throw new Error('Please define an entity');
    }
    this.config = config;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this.content) {
      this.render();
    }
    this.updateCard();
  }

  updateCard() {
    const entityId = this.config.entity;
    const stateObj = this._hass.states[entityId];
    
    if (!stateObj) return;

    // Get custom descriptions from entity attributes
    const openDesc = stateObj.attributes.open_description || "Öffnen";
    const closeDesc = stateObj.attributes.close_description || "Schließen";
    const stopDesc = stateObj.attributes.stop_description || "Stopp";
    const supportsStop = stateObj.attributes.supported_features & 4; // STOP feature

    // Update button tooltips
    const openBtn = this.shadowRoot.querySelector('.open-btn');
    const closeBtn = this.shadowRoot.querySelector('.close-btn');
    const stopBtn = this.shadowRoot.querySelector('.stop-btn');

    if (openBtn) openBtn.title = openDesc;
    if (closeBtn) closeBtn.title = closeDesc;
    if (stopBtn) {
      stopBtn.title = stopDesc;
      stopBtn.style.display = supportsStop ? 'block' : 'none';
    }

    // Update entity name
    const nameEl = this.shadowRoot.querySelector('.entity-name');
    if (nameEl) {
      nameEl.textContent = stateObj.attributes.friendly_name || entityId;
    }

    // Update icon
    const iconEl = this.shadowRoot.querySelector('ha-icon');
    if (iconEl && stateObj.attributes.icon) {
      iconEl.icon = stateObj.attributes.icon;
    }
  }

  render() {
    this.shadowRoot.innerHTML = `
      <style>
        .card {
          padding: 16px;
          background: var(--ha-card-background, var(--card-background-color, white));
          border-radius: var(--ha-card-border-radius, 12px);
          box-shadow: var(--ha-card-box-shadow, none);
          border: var(--ha-card-border-width, 1px) solid var(--ha-card-border-color, var(--divider-color));
        }
        .header {
          display: flex;
          align-items: center;
          margin-bottom: 16px;
        }
        .entity-name {
          margin-left: 8px;
          font-weight: 500;
          color: var(--primary-text-color);
        }
        .controls {
          display: flex;
          gap: 8px;
          justify-content: space-around;
        }
        .control-btn {
          flex: 1;
          padding: 12px;
          border: none;
          border-radius: 8px;
          background: var(--primary-color);
          color: var(--text-primary-color);
          cursor: pointer;
          transition: background-color 0.2s;
          font-size: 14px;
        }
        .control-btn:hover {
          background: var(--primary-color-dark);
        }
        .control-btn:disabled {
          background: var(--disabled-color);
          cursor: not-allowed;
        }
        ha-icon {
          --mdc-icon-size: 24px;
          color: var(--primary-text-color);
        }
      </style>
      <div class="card">
        <div class="header">
          <ha-icon></ha-icon>
          <span class="entity-name"></span>
        </div>
        <div class="controls">
          <button class="control-btn open-btn">▲</button>
          <button class="control-btn close-btn">▼</button>
          <button class="control-btn stop-btn">⏹</button>
        </div>
      </div>
    `;

    this.content = true;
    
    // Add event listeners
    const openBtn = this.shadowRoot.querySelector('.open-btn');
    const closeBtn = this.shadowRoot.querySelector('.close-btn');
    const stopBtn = this.shadowRoot.querySelector('.stop-btn');

    openBtn.addEventListener('click', () => this.callService('open_cover'));
    closeBtn.addEventListener('click', () => this.callService('close_cover'));
    stopBtn.addEventListener('click', () => this.callService('stop_cover'));
  }

  callService(service) {
    this._hass.callService('cover', service, {
      entity_id: this.config.entity
    });
  }

  getCardSize() {
    return 2;
  }
}

customElements.define('eldat-cover-card', EldatCoverCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: 'eldat-cover-card',
  name: 'ELDAT Cover Card',
  description: 'Custom card for ELDAT cover entities with proper tooltips'
});