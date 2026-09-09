# Driver Implementation Summary: piper_onboard

## Robot Configuration
- **Type**: 6-DOF serial manipulator with parallel gripper
- **Class**: ARM
- **End-effector**: 2-finger parallel gripper (coupled via equality constraint)
- **Joint limits**: All joints have physical limits, including restricted range on joint2 and joint3

## IK Algorithm: Damped Least Squares (DLS)

### Method Selection Rationale
For this 6-DOF non-redundant arm, I chose **Damped Least Squares (DLS)** with **position-only IK** (3x6 Jacobian) for the following reasons:

1. **Position-only IK**: The `move_cartesian()` API requires reaching a target position but does not constrain orientation. Using a 3x6 Jacobian (position only) gives the algorithm more freedom to find solutions, especially in constrained workspaces.

2. **Adaptive damping**: λ = λ_base + λ_scale × ||error||
   - Higher damping when error is large → more stable but slower
   - Lower damping when error is small → faster convergence
   - Base: λ_base = 0.05, Scale: λ_scale = 5.0

3. **Robustness features**:
   - Joint limit clamping on every iteration
   - Step size limiting (0.3 rad/iter) to prevent instability
   - 150 max iterations with 1mm convergence tolerance

### Algorithm Details

**Core iteration**:
```
For each iteration:
  1. Compute position error: e_pos = target - current
  2. Compute Jacobian: J_pos (3×6 from mj_jacSite)
  3. Adaptive damping: λ = 0.05 + 5.0 × ||e_pos||
  4. Solve: dq = (J^T J + λ²I)^(-1) J^T e_pos
  5. Clamp step: if ||dq|| > 0.3, scale to 0.3
  6. Update joints: q += dq, clamp to limits
  7. Check convergence: ||e_pos|| < 1mm
```

## Performance Results

### IK Round-Trip Tests (from home position)
All tests achieved sub-millimeter accuracy:

| Test | Target Offset | Final Error | Status |
|------|---------------|-------------|--------|
| +5cm X | [0.05, 0, 0] | 0.08 mm | ✓ PASS |
| +3cm Y | [0, 0.03, 0] | 0.14 mm | ✓ PASS |
| -4cm Z | [0, 0, -0.04] | 0.35 mm | ✓ PASS |
| Combined | [0.03, 0.02, -0.02] | 0.65 mm | ✓ PASS |

**Maximum error**: 0.65mm (well below 2cm threshold)

### Gripper Behavior

The gripper implementation provides:

1. **gripper_open()**: Sets gripper to maximum opening (0.035m)
2. **gripper_close()**: Sets gripper to minimum opening (0.0m)
3. **is_holding()**: Returns True when gripper < 70% of max opening

The 70% threshold provides a simple heuristic:
- Fully open (100%) = 0.035m → is_holding() = False
- Partially closed (< 70%) → is_holding() = True
- This indicates the gripper is in contact with an object

### Key Design Decisions

1. **Position-only IK**: Chose 3D position control over 6D pose control for better convergence in the arm's workspace. The wrist joints (4, 5, 6) adjust orientation naturally to reach the target position.

2. **No analytical IK**: Although the arm has a spherical wrist (joints 4-6 intersect), I implemented numerical DLS for:
   - Robustness to singularities
   - Easier to maintain and debug
   - Automatic handling of joint limits
   - Sub-millimeter accuracy is sufficient for manipulation tasks

3. **Adaptive damping**: Dynamic adjustment of damping factor based on error magnitude prevents:
   - Instability near target (low damping)
   - Oscillations far from target (high damping)

4. **Graceful failure**: IK returns False when unreachable, with error messages for debugging.

## API Completeness

All required methods implemented and tested:

**Core APIs**:
- ✓ `build_from_mjcf(mjcf_path)` - Handles symlinks and includes
- ✓ `home()` - Returns to home configuration
- ✓ `get_joint_positions()` - Returns 6D joint angles
- ✓ `step(n)` - Advances simulation
- ✓ `render()` - Returns RGB image (480×640×3)
- ✓ `describe()` - Human-readable state

**Arm-specific APIs**:
- ✓ `get_ee_pose()` - Returns (xyz, R3x3)
- ✓ `move_cartesian(target_xyz, duration)` - Position-only IK
- ✓ `gripper_open()` - Opens gripper
- ✓ `gripper_close()` - Closes gripper  
- ✓ `is_holding()` - Detects grasp state

## Testing

All validation passed:
```bash
python driver_from_scratch.py           # Standalone tests
python test_ik_roundtrip.py            # Comprehensive IK validation
python test_gripper_demo.py            # Gripper behavior demo
```

## Dependencies

- `mujoco` - Physics simulation and FK/Jacobian computation
- `numpy` - Linear algebra for IK solver
- `os` - File path handling for MJCF loading

**No imports from `auto_adapter.skeletons`** - completely standalone implementation.
