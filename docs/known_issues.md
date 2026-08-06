# Known Issues

## Audio Injector Octo on newer Raspberry Pi kernels

The Audio Injector Octo can be detected by Linux while still producing pulsing, distorted, unstable, or silent playback. The likely fault boundary includes Raspberry Pi I2S clocking, ASoC machine-driver behavior, device-tree overlay compatibility, and kernel changes.

### Current deployment rule

- Begin with a tested Raspberry Pi OS and kernel image.
- Pin the known-good kernel and boot configuration after acceptance.
- Do not upgrade the kernel automatically on a deployed audio node.
- Record the exact image, kernel, overlay, ALSA card identity, supported formats, rates, and channel tests in an acceptance receipt.
- Treat successful card enumeration as insufficient proof. Every input and output must pass an audio test.

### Failure posture

If the Octo is detected but fails acceptance, the studio reports `DEGRADED` or `UNAVAILABLE`. It must not advertise healthy channels to Velvet Runtime. Safety alerts must fall back to another verified output where available.

### Future unit exchange

The Octo is the first multichannel unit, not a permanent architectural dependency. A future interface may replace it when:

- kernel support becomes unreliable;
- replacement boards become scarce;
- another unit offers better supported multichannel I/O;
- a USB, network, or PCIe audio interface becomes more suitable;
- the vehicle requires a different channel count or electrical interface.

Replacement must occur through the hardware adapter and capability descriptor. Studio bookings, priority rules, session management, event contracts, receipts, and logical channel names must remain unchanged.

## Direct ALSA access

Features must not open the Octo PCM directly. Direct access bypasses booking, ducking, health, and receipts, and can seize the single multichannel stream from the studio engine.
