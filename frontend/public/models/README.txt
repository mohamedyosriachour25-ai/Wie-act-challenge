Face-detection models used by the opt-in camera attention check (src/lib/attention.js).
BlazeFace (face detector) and FaceMesh (landmarks, for head pose) from the @vladmandic/human package (MIT, see LICENSE-human.txt).
They are served by this app itself so no image and no model request ever goes to a third party.
