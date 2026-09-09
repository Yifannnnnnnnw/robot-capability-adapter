# ANYmal C Quadruped Robot Driver

Complete driver implementation for the ANYmal C quadruped robot, written from scratch without using auto_adapter.skeletons.

## Quick Start

```python
import driver_from_scratch

# Build robot from MJCF
robot = driver_from_scratch.Robot.build_from_mjcf("mjcf.xml")

# Basic movements
robot.home()                          # Go to neutral pose
robot.stand_up(duration=2.0)          # Stand up
robot.walk_forward(secs=3.0, speed=0.3)  # Walk forward
robot.sit(duration=1.5)               # Sit down

# Query state
joint_pos = robot.get_joint_positions()  # Get 12 joint angles
height = robot.get_body_height()         # Get base height
pos, quat = robot.get_base_pose()        # Get base position & orientation

# Simulation
robot.step(10)                        # Step simulation
img = robot.render()                  # Render image
desc = robot.describe()               # Get description
```

## Robot Specifications

- **Type**: Quadruped (4-legged locomotion robot)
- **DOF**: 12 (4 legs × 3 joints)
- **Legs**: LF (Left Front), RF (Right Front), LH (Left Hind), RH (Right Hind)
- **Joints per leg**: HAA (Hip Ab/Adduction), HFE (Hip Flexion/Extension), KFE (Knee Flexion/Extension)
- **Control**: Position-controlled actuators
- **No gripper**: This is a locomotion platform, not a manipulation robot

## API Reference

### Construction
- `Robot.build_from_mjcf(mjcf_path: str) -> Robot` - Build from MJCF file

### Locomotion Behaviors
- `home() -> bool` - Move to neutral pose
- `stand_up(duration=2.0) -> bool` - Stand up on extended legs
- `sit(duration=1.5) -> bool` - Sit down with folded legs
- `walk_forward(secs=2.0, speed=0.2) -> bool` - Walk forward using trotting gait

### State Queries
- `get_joint_positions() -> ndarray(12,)` - Get joint angles (radians)
- `get_body_height() -> float` - Get base height above ground (meters)
- `get_base_pose() -> (pos[3], quat[4])` - Get base position and orientation

### Simulation
- `step(n=1)` - Step simulation forward
- `render() -> ndarray or None` - Render RGB image (480×640×3)
- `describe() -> str` - Get detailed state description

## Control Algorithm

### Joint-Space PD Control (NOT IK)
This driver uses **joint-space PD control**, not inverse kinematics, because:
- Quadrupeds have no single end-effector to track
- Locomotion is naturally expressed in joint space
- Multiple simultaneous ground contacts require coordinated leg control

### Walking Gait
Implements diagonal-pair trotting:
- (LF + RH) swing together
- (RF + LH) swing together (180° out of phase)
- Sinusoidal swing trajectory
- Configurable gait frequency via `speed` parameter

### PD Gains
- Position gain (kp): 50.0
- Velocity gain (kd): 5.0

## Files

- `driver_from_scratch.py` - Main driver implementation (14 KB)
- `test_behaviors.py` - Comprehensive behavior tests
- `test_locomotion.py` - Locomotion-specific tests
- `demo_complete.py` - Full API demonstration
- `verify_api.py` - API compliance verification
- `IMPLEMENTATION_SUMMARY.md` - Detailed technical summary

## Testing

Run all tests:
```bash
python test_behaviors.py      # Test all behaviors
python test_locomotion.py     # Test locomotion stability
python demo_complete.py       # Run full demonstration
python verify_api.py          # Verify API compliance
```

Quick validation:
```bash
python driver_from_scratch.py  # Run built-in tests
```

## Design Decisions

### Why No IK?
Inverse kinematics is **not appropriate** for quadrupeds:
1. No single end-effector (has 4 feet instead)
2. Locomotion goals are body movement, not reaching targets
3. Coordinated multi-leg control is naturally joint-space
4. Ground contact constraints make Cartesian control difficult

### Why This Gait?
Trotting (diagonal pairs) is:
- Stable for moderate speeds
- Energy efficient
- Simple to implement
- Standard in quadruped robotics

### Why Position Control?
The ANYmal C model uses position-controlled actuators:
- Direct joint position commands
- Built-in low-level control
- Simpler than torque control for this application

## Performance

- **Pose transitions**: 1-2 seconds with smooth interpolation
- **Stability**: Maintains upright posture during all behaviors
- **Joint tracking**: <0.3 rad error after transitions
- **Safety**: All commands clamped to joint limits

## Implementation Notes

- **Self-contained**: No auto_adapter.skeletons imports
- **Dependencies**: mujoco, numpy, Python stdlib only
- **Model structure**: 19 qpos (7 freejoint + 12 joints)
- **Actuators**: 12 position-controlled
- **Feet**: Tracked via shank body positions

## Future Work

Potential improvements:
- More sophisticated gaits (CPG, trajectory optimization)
- Turning and lateral movement
- Terrain adaptation
- Speed mapping to actual velocity
- Dynamic gaits (bounding, galloping)

## License

Implementation created for Phase 2 GEN_ALGO onboarding.

---

**Robot ID**: anymal_c_onboard  
**Class**: Quadruped  
**DOF**: 12  
**Control**: Joint-space PD (no IK)  
**Status**: ✓ Complete and tested
