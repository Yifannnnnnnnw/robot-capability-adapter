"""
Validation script for so101_full robot.
Tests IK roundtrip and grasp-lift behavior with video recording.
"""
import driver
import time
import json
import numpy as np
import imageio
import traceback

def main():
    # Build the skeleton
    skel = driver.build()
    
    # Test 1: IK Roundtrip
    test_name = "ik_roundtrip"
    frames = []
    ts = int(time.time())
    recording_path = f"recordings/validate_{test_name}_{ts}.mp4"
    
    try:
        # Get home pose (returns tuple of pos, rotmat)
        home_pos, home_rotmat = skel.get_ee_pose()
        
        # Target position: move +0.05 in X and +0.05 in Z
        target_pos = home_pos.copy()
        target_pos[0] += 0.05
        target_pos[2] += 0.05
        
        # Move to target (only takes position, not rotation)
        skel.move_cartesian(target_pos, duration=2.0)
        
        # Capture frames during settling
        for _ in range(12):
            skel.step(5)
            frame = skel.render()
            frames.append(frame)
        
        # Read back pose
        actual_pos, actual_rotmat = skel.get_ee_pose()
        
        # Calculate position error
        pos_error = float(np.linalg.norm(actual_pos - target_pos))
        
        # Save recording
        imageio.mimsave(recording_path, frames, fps=30, codec="libx264")
        
        ok = pos_error < 0.01
        result = {
            "test": test_name,
            "ok": ok,
            "detail": f"Position error: {pos_error:.6f} m",
            "metric": pos_error,
            "recording": recording_path
        }
        print(json.dumps(result))
        
    except Exception as e:
        traceback.print_exc()
        if frames:
            try:
                imageio.mimsave(recording_path, frames, fps=30, codec="libx264")
            except:
                pass
        result = {
            "test": test_name,
            "ok": False,
            "detail": f"Exception: {str(e)}",
            "metric": 999.0,
            "recording": recording_path if frames else ""
        }
        print(json.dumps(result))
    
    # Test 2: Grasp and Lift
    test_name = "grasp_lift"
    frames = []
    ts = int(time.time())
    recording_path = f"recordings/validate_{test_name}_{ts}.mp4"
    
    try:
        # Move back to home position first
        skel.home()
        for _ in range(5):
            skel.step(10)
        
        # Get first graspable body position
        target_body = "banana"  # First in graspable_bodies
        body_pos = skel.get_object_position(target_body)
        
        # Approach position: above the object
        approach_pos = body_pos.copy()
        approach_pos[2] += 0.05  # 5cm above
        
        # Open gripper
        skel.gripper_open()
        for _ in range(3):
            skel.step(5)
            frame = skel.render()
            frames.append(frame)
        
        # Move to approach
        skel.move_cartesian(approach_pos, duration=2.0)
        for _ in range(10):
            skel.step(5)
            frame = skel.render()
            frames.append(frame)
        
        # Close gripper
        skel.gripper_close()
        for _ in range(5):
            skel.step(5)
            frame = skel.render()
            frames.append(frame)
        
        # Get body position before lift
        body_pos_before_arr = skel.get_object_position(target_body)
        body_pos_before = float(body_pos_before_arr[2])
        
        # Lift up
        current_pos, current_rotmat = skel.get_ee_pose()
        lift_pos = current_pos.copy()
        lift_pos[2] += 0.10  # Lift 10cm
        skel.move_cartesian(lift_pos, duration=2.0)
        
        for _ in range(15):
            skel.step(5)
            frame = skel.render()
            frames.append(frame)
        
        # Get body position after lift
        body_pos_after_arr = skel.get_object_position(target_body)
        body_pos_after = float(body_pos_after_arr[2])
        
        # Calculate lift height
        lift_height = body_pos_after - body_pos_before
        
        # Save recording
        imageio.mimsave(recording_path, frames, fps=30, codec="libx264")
        
        ok = lift_height > 0.05
        result = {
            "test": test_name,
            "ok": ok,
            "detail": f"Lifted {lift_height:.4f} m (before: {body_pos_before:.4f}, after: {body_pos_after:.4f})",
            "metric": lift_height,
            "recording": recording_path
        }
        print(json.dumps(result))
        
    except Exception as e:
        traceback.print_exc()
        if frames:
            try:
                imageio.mimsave(recording_path, frames, fps=30, codec="libx264")
            except:
                pass
        result = {
            "test": test_name,
            "ok": False,
            "detail": f"Exception: {str(e)}",
            "metric": 0.0,
            "recording": recording_path if frames else ""
        }
        print(json.dumps(result))
    
    print("___END___")

if __name__ == "__main__":
    main()
