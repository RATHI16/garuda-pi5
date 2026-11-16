# Robust target re-identification for person follow (Pi 5 + Hailo-10H + OAK stereo)

Status: design, 2026-11-14. Owner requirement, verbatim: "the reid has to be really roboust"
and "it has to pick one when many are there". Bench and simulation only.

## Why

In the stereo lab the follow ended, and the Gazebo figure vanished, whenever the tracker
gave the selected person a new track ID. The only appearance cue today is a 128-bin
clothing-colour histogram (`hosts/pi5/mono_appearance.py`). It cannot separate people in
similar clothes and has never been measured for wrong matches.

## Goal

Keep following the one selected person through track-ID changes, occlusions and leaving and
re-entering the view, while other people are present.

## Anti-goal (what makes this a failure even if it "works")

Following the wrong person. A wrong bind is worse than a hold. When the evidence does not
single out the selected person, the system holds and asks for a click.

## Non-goals

- Naming or recognising people across sessions. Identity lives for one selection, in memory.
- Real flight. Nothing here changes the simulation-only gates.
- Validating stereo distance. Geometry cues use the unvalidated distance as a relative cue.

## Design

One identity decision, made on the Pi in the detector, so the page, the PX4 lab and the
camera's stereo lock all see the same answer. The page's and the lab's own colour-histogram
return matches defer to it when it is present.

### Cues

1. **Learned appearance (primary).** OSNet x1.0 person re-ID embedding (512-d, L2
   normalised) on the Hailo-10H for up to 4 person crops per frame, from the same frame as
   detection. Pinned model-zoo HEF for HailoRT 5.3.0 (see `models/osnet_h10.md`).
2. **Stereo position gate.** A candidate must be where the selected person could have got
   to: distance from the last measured position <= max walking speed x time since last seen
   + margin. Needs a stereo reading for the candidate; without one this cue is "unknown",
   never "pass".
3. **Metric height.** Person height from box height x stereo depth / focal length, compared
   with the height learned while locked. A soft cue with a wide tolerance (pose changes it).
4. **Co-visibility exclusion (hard).** A track that was visible at the same time as the
   selected person is not that person, for as long as that track ID lives.
5. **Colour histogram.** Tie-breaker only; never sufficient alone.
6. **Face (optional, existing).** When the operator has enrolled a face, it can confirm.

### Rules (carried over from the Hailo-15L identity work, which measured them)

- An immutable anchor embedding from the selection moment plus a small bounded gallery of
  recent confirmed views. The gallery only grows from frames where the lock is certain.
- Binding the selection to a DIFFERENT track ID requires appearance on both sides and all
  available gates passing. Missing evidence refuses the bind; it never counts as a pass.
- The best candidate must beat the runner-up by a margin, for several consecutive frames
  and a minimum time. Similar candidates mean hold.
- Identity memory expires (30 s by default); after that only a click re-selects.
- OSNet alone does not separate look-alikes (measured on the Hailo-15L). That is why the
  position, height and co-visibility cues are part of the decision, not decoration.

### Fewer ID changes in the first place (second stage)

Use the embedding inside track association, so a brief occlusion or a crossing does not
hand the ID to someone else or drop it. Separate stage because it changes the tracker.

## Measurement before trust

Recorded bench sessions (logs only: boxes, embeddings, stereo readings, decisions; no
images in Git) with scripted scenarios: alone out-and-in; second person standing; two people
crossing; similar clothing; several people moving. Scored for wrong binds (target: none),
missed returns, and time to re-acquire. Thresholds are chosen from these recordings, not
guessed, and every threshold is recorded with the run it came from.

## Stages and gates

1. Bring OSNet up on the Pi beside the detector; measure its cost. Gate: detector stays at
   or near 15 frames/s with up to 4 crops per frame; otherwise fewer crops or every other frame.
2. Recorder and offline scorer; record the scenarios.
3. Identity owner on the Pi with cues 1-5, thresholds from stage 2. Gate: no wrong bind in
   the recorded scenarios.
4. Page and lab defer to the Pi's identity decision.
5. Appearance-aided association in the tracker.

## Licence

OSNet source: deep-person-reid, MIT. The model-zoo weights are trained on Market1501
(research dataset). EVAL-ONLY, not-product-cleared, behind the detector's existing
plugin boundary; revisit before any product use.

## Open decisions for the owner

- Maximum walking speed assumed for the position gate (proposal: 3 m/s).
- Whether a hold-and-ask after 30 s is acceptable at a demo, or the memory should be longer.
