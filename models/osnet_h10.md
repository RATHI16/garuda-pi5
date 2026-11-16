# OSNet person re-identification on Hailo-10H — bench only

Pinned Hailo model-zoo artifact for HailoRT 5.3.0 (the Pi's runtime):
`https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled/v5.3.0/hailo10h/osnet_x1_0.hef`
Size 3,948,544 bytes. SHA-256 `5c376b5e16cc42d8e5511aad649cc74b9503d4f44911a28ab157cbe899db1d39`
(fetched 2026-11-14; same hash on the Pi).

Source: deep-person-reid (MIT). Weights trained on Market1501. **EVAL-ONLY,
not-product-cleared.** Vendor figures (rank-1 93.5 on hardware, 292 FPS batch 1, x86 host)
are not measurements on this bench.

`hailortcli parse-hef` on the Pi (HailoRT 5.3.0): compatible HAILO15H/HAILO10H, compiler 5.3.0,
4 contexts, input `osnet_x1_0/input_layer1` UINT8 NHWC(256x128x3), output `osnet_x1_0/fc1` NC(512).
The wrapper feeds RGB crops; whether normalisation is inside the HEF is assumed from the
Hailo-15L card, not checked here (TBD-bench).
Design: `docs/superpowers/specs/2026-11-14-robust-target-reid-design.md`.
