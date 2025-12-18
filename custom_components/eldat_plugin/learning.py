"""Generic learning helper module for ELDAT integration.

Centralizes device learning logic so ConfigFlow, OptionsFlow and services
can reuse the same implementation and benefit from cancellation support.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, Dict

_LOGGER = logging.getLogger(__name__)

async def async_perform_device_learning(
    coordinator, 
    device_type: str, 
    timeout: int, 
    cancel_event: asyncio.Event | None = None
) -> Optional[Dict[str, any]]:
    """Perform device learning via EWB_RCV with optional cancellation.

    Args:
        coordinator: EldatCoordinator instance.
        device_type: Expected device type selected by user.
        timeout: Max seconds to wait for first telegram/device.
        cancel_event: If provided and set() is called, abort early.
    Returns:
        Enhanced device info dict or None if timeout / cancelled.
    """
    # Get the coordinator's transceiver
    transceiver = coordinator.transceiver
    if not transceiver:
        _LOGGER.error("No transceiver available for learning")
        return None

    _LOGGER.info("[learn] Starting learning for %s (timeout=%ds, cancelable=%s)", device_type, timeout, bool(cancel_event))

    # Start learning mode
    await transceiver.start_learning_mode()

    start = asyncio.get_event_loop().time()
    learned: Optional[Dict] = None

    try:
        # Single-shot mode: underlying helper already blocks until telegram or timeout
        # We wrap in cancellation logic if a cancel_event is provided.
        async def _do_learn():
            return await transceiver.start_telegram_listening(callback)

        learn_task = asyncio.create_task(_do_learn())

        if cancel_event:
            done, pending = await asyncio.wait(
                {learn_task, asyncio.create_task(cancel_event.wait())},
                return_when=asyncio.FIRST_COMPLETED
            )
            # If cancel_event finished first, abort
            if cancel_event.is_set() and learn_task not in done:
                _LOGGER.info("[learn] Cancel event triggered before device learned")
                for p in pending:
                    p.cancel()
                learn_task.cancel()
                try:
                    await learn_task
                except asyncio.CancelledError:
                    pass
                return None
        else:
            # Wait normally
            learned = await learn_task

        if learned:
            info_type = learned.get("info_type", 0)
            serial_number = learned.get("serial")
            enhanced = coordinator._categorize_device_by_info_type(learned, device_type, info_type)

            if serial_number and serial_number not in coordinator.devices:
                coordinator.devices[serial_number] = enhanced
                await coordinator._register_new_device(serial_number, enhanced)
                await coordinator.transceiver.register_device(serial_number, enhanced)
                coordinator._known_devices.add(serial_number)
                await coordinator._save_device_configuration()
                await coordinator.async_request_refresh()
            _LOGGER.info("[learn] Device learned successfully: %s", enhanced.get("name"))
            return enhanced
        else:
            _LOGGER.info("[learn] No device learned (timeout or cancelled)")
            return None
    except Exception as e:
        _LOGGER.error("[learn] Error during device learning: %s", e)
        return None
    finally:
        # CRITICAL: Restore the main coordinator callback after learning
        # Learning process might have overridden it with a temporary callback
        if hasattr(transceiver, 'set_telegram_callback') and hasattr(coordinator, '_handle_telegram'):
            _LOGGER.info("🔗 Restoring coordinator telegram callback after learning")
            transceiver.set_telegram_callback(coordinator._handle_telegram)
        return None
    finally:
        # Always disable learning mode
        await transceiver.stop_learning_mode()
        elapsed = asyncio.get_event_loop().time() - start
        _LOGGER.debug("[learn] Finished learning attempt (elapsed=%.2fs)", elapsed)
