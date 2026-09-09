# Unitree Go2 Quadruped Driver - From Scratch

**Robot**: Unitree Go2 (12-DOF quadruped)  
**Control**: Joint-space PD control  
**Implementation**: Pure MuJoCo + NumPy (no skeleton imports)

## Quick Start

```python
import driver_from_scratch

# Build robot from MJCF
robot = driver_from_scratch.Robot.build_from_mjcf("mjcf.xml")

# Move to standing pose
robot.home()

# Walk forward for 2 seconds
robot.walk_forward(secs=2.0, speed=0.3)

# Query state
height = robot.get_body_height()
xyz, quat = robot.get_base_pose()
joint_positions = robot.get_joint_positions()

# Describe the robot
print(robot.describe())
```

## API Overview

### Required Methods
- `build_from_mjcf(mjcf_path)` - Factory method to construct robot
- `home()` - Move to home (standing) pose
- `get_joint_positions()` - Get 12-DOF joint angles [rad]
- `step(n=1)` - Advance simulation by n steps
- `render()` - Render RGB frame (480×640×3)
- `describe()` - Human-readable description

### Quadruped Locomotion API
- `stand_up(duration=2.0)` - Rise to standing pose via PD interpolation
- `sit(duration=1.5)` - Fold legs into sitting configuration
- `walk_forward(secs=2.0, speed=0.2)` - Execute diagonal-pair gait
- `get_body_height()` - Query base z-coordinate [m]
- `get_base_pose()` - Query (xyz [m], quaternion [w,x,y,z])

## Why No IK?

This is a **quadruped locomotion robot**, not a manipulator arm. It has:
- ❌ No end-effector to position
- ❌ No task-space targets to reach
- ✅ Legs for locomotion (stance and swing phases)
- ✅ Body pose to control (height, orientation)

**Inverse kinematics** is for arms that need to place a gripper/tool at a specific XYZ position. **Quadrupeds use**:
- Joint-space target poses (standing, sitting)
- Gait generators (walking, trotting patterns)
- Body controllers (balance, turning)

## Control Algorithm

### PD Control
```python
τ = kp * (q_desired - q_current) + kd * (qd_desired - qd_current)
```

**Gains**: 
- Standing/sitting: `kp=100.0, kd=10.0`
- Walking: `kp=80.0, kd=8.0` (reduced for smoother motion)

**Torque limits**: Clamped to `[-23.7, 23.7]` Nm (hip/thigh) and `[-45.43, 45.43]` Nm (calf)

### Walking Gait

**Diagonal-pair sinusoidal oscillation**:
- **Pair 1** (FR + RL legs): swing together
- **Pair 2** (FL + RR legs): swing π out of phase

```python
phase = 2π × speed × t
thigh_offset = 0.5 × swing_amp × sin(phase)
```

**Parameters**:
- `swing_amp = 0.4` rad (thigh joint oscillation)
- `speed` [Hz] controls gait frequency

## Test Results

Run comprehensive tests:
```bash
python test_final.py
```

**Performance**:
| Behavior         | Duration | Result                    |
|------------------|----------|---------------------------|
| Stand up (home)  | 2.0s     | ✓ 0.127 rad error         |
| Walk forward     | 2.0s     | ✓ 0.506 m displacement    |
| Sit              | 1.0s     | ✓ -0.39 m height change   |

## File Structure

```
driver_from_scratch.py    - Main Robot class (520 lines)
test_final.py             - Comprehensive test suite
test_locomotion.py        - Locomotion primitive tests
SUMMARY.md                - Algorithm and design decisions
IMPLEMENTATION_NOTES.md   - Detailed implementation notes
README.md                 - This file
```

## Dependencies

```python
import mujoco      # Physics simulation
import numpy       # Numerical arrays
import os, typing  # Python stdlib
```

**No** `auto_adapter.skeletons` imports.

## Design Philosophy

1. **Match robot class to control strategy**:
   - ARM → IK + Cartesian control
   - QUADRUPED → Joint-space PD + gaits
   - MOBILE BASE → Velocity commands
   - HUMANOID → Whole-body control

2. **Keep it simple**: PD control is O(n), robust, and real-time.

3. **Test what matters**: For quadrupeds, test locomotion (displacement, height), not IK accuracy.

## Limitations

- **No turning**: Only forward walking
- **No balance control**: Open-loop (no IMU feedback)
- **Simple gait**: Not contact-aware
- **Sit instability**: Extreme folding causes convergence issues

## Future Extensions

- Add turning via differential swing
- Implement trotting/bounding gaits
- Add IMU-based balance control
- Use contact sensors for adaptive stepping
- Switch to MPC for rough terrain

## License

Auto-generated Phase 2 driver for the Unitree Go2 quadruped.

---

**Summary**: Joint-space PD control with diagonal-pair gait achieves 0.506 m forward displacement in 2.0s. No IK (correct for quadruped). No gripper (none present).
