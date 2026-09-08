# Cooking quantity control

The crops come from the user's PC recording `2026-08-06 23-42-51.mp4`.
Frames are normalized to 1920 × 1080 before cropping; the retained region is
`(300, 970)-(730, 1035)` and contains only the quantity slider.

- `min.png`: 4 seconds, quantity 1, handle at the left.
- `max.png`: 17 seconds, another recipe at MAX, handle at the right.

The production endpoint template uses a separate frame at 5 seconds, cropped
at `(662, 971)-(726, 1031)`. The regression checks the actual matcher at 1080p,
720p and 1191 × 669, alongside the click-loss behavior test.
