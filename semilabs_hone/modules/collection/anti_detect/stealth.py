"""Layer 3 — minimal Canvas/Audio micro-noise injection.

Does NOT touch WebGL, navigator.webdriver, plugins, or languages.
Only adds pixel-level jitter to Canvas toDataURL/getImageData and
AudioContext getChannelData to break fingerprint stability.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext

NOISE_ONLY_SCRIPT: str = r"""
// Canvas toDataURL — per-pixel ±1 random noise (imperceptible)
(function() {
    const _toDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type) {
        if (type === 'image/png' || !type) {
            try {
                const ctx = this.getContext('2d');
                if (ctx) {
                    const imgData = ctx.getImageData(0, 0, this.width, this.height);
                    const d = imgData.data;
                    for (let i = 0; i < d.length; i += 4) {
                        d[i]     += (Math.random() * 2 - 1) | 0;  // R ±1
                        d[i + 1] += (Math.random() * 2 - 1) | 0;  // G ±1
                        d[i + 2] += (Math.random() * 2 - 1) | 0;  // B ±1
                    }
                    ctx.putImageData(imgData, 0, 0);
                }
            } catch (e) {}
        }
        return _toDataURL.apply(this, arguments);
    };
})();

// Canvas getImageData — per-pixel ±1 random noise
(function() {
    const _getImageData = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = function(sx, sy, sw, sh) {
        const imgData = _getImageData.apply(this, arguments);
        const d = imgData.data;
        for (let i = 0; i < d.length; i += 4) {
            d[i]     += (Math.random() * 2 - 1) | 0;
            d[i + 1] += (Math.random() * 2 - 1) | 0;
            d[i + 2] += (Math.random() * 2 - 1) | 0;
        }
        return imgData;
    };
})();

// AudioContext getChannelData — tiny per-sample noise
(function() {
    if (typeof AudioBuffer === 'undefined') return;
    const _getChannelData = AudioBuffer.prototype.getChannelData;
    AudioBuffer.prototype.getChannelData = function(index) {
        const data = _getChannelData.call(this, index);
        for (let i = 0; i < data.length; i++) {
            data[i] += (Math.random() - 0.5) * 0.00001;
        }
        return data;
    };
})();
"""


async def inject_noise(ctx: "BrowserContext") -> None:
    """Inject the noise-only script into every new page via init_script."""
    await ctx.add_init_script(NOISE_ONLY_SCRIPT)
