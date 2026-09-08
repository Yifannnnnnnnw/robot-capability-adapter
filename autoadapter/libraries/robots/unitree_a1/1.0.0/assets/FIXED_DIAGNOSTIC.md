# Fixed-family diagnostic scene

The original `scene.xml`, `a1.xml`, licenses and source revision are retained. `fixed_scene.xml` includes `fixed_robot.xml`; the only robot physics change is the four foot collision geoms' `solimp`, changed from `0.015 1 0.02` to `0.9 0.95 0.001 0.5 2`. No mass, friction, actuator gain, control limit, home joint target or policy weight is changed. Both public development and private capability instances use this scene.

A separate `fixed_settled` keyframe records the full pose after 3 seconds of native position control at the original `home` controls, initially placing the lowest actual foot 0.5 mm above the floor. Reset velocity is zero. The original `home` keyframe remains intact, including Barkour's policy action center where applicable. Settled base height is 0.260802876 m. G4 request range is 80–120% of this height and its two targets remain 94% and 108%; absolute acceptance tolerances are unchanged. G5 applies the original 5°/8° disturbance to the settled pose and only raises the base enough to avoid initial foot-floor overlap.

The calibration data is in `../capability_validation/private/fixed_environment_calibration.json`. It describes environment preparation, not model-generated Driver success or full reference capability success.
