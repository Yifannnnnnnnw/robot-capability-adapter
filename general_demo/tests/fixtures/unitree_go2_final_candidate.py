import math
import time

def capability_cap_stand_up(*, _sdk):
    STAND_Q = [0.0, 0.9, -1.8, 0.0, 0.9, -1.8, 0.0, 0.9, -1.8, 0.0, 0.9, -1.8]
    STAND_KP = 60.0
    STAND_KD = 3.0
    
    factory_init = _sdk.ChannelFactoryInitialize()
    cmd_pub = _sdk.ChannelPublisher('rt/lowcmd', _sdk.LowCmd_)
    cmd_pub.Init()
    state_sub = _sdk.ChannelSubscriber('rt/lowstate', _sdk.LowState_)
    state_sub.Init()
    
    start_time = time.time()
    state = state_sub.Read()
    q_start = [state.motor_state[i].q for i in range(12)]
    duration = 2.0
    
    while time.time() - start_time < duration:
        alpha = (time.time() - start_time) / duration
        alpha = min(1.0, alpha)
        q_des = [q_start[i] + alpha * (STAND_Q[i] - q_start[i]) for i in range(12)]
        
        cmd = _sdk.unitree_go_msg_dds__LowCmd_()
        for i in range(12):
            cmd.motor_cmd[i].mode = 1
            cmd.motor_cmd[i].q = float(q_des[i])
            cmd.motor_cmd[i].dq = 0.0
            cmd.motor_cmd[i].kp = float(STAND_KP)
            cmd.motor_cmd[i].kd = float(STAND_KD)
            cmd.motor_cmd[i].tau = 0.0
        for i in range(12, 20):
            cmd.motor_cmd[i].mode = 1
            cmd.motor_cmd[i].q = 2146000000.0
            cmd.motor_cmd[i].dq = 16000.0
            cmd.motor_cmd[i].kp = 0.0
            cmd.motor_cmd[i].kd = 0.0
            cmd.motor_cmd[i].tau = 0.0
        cmd.crc = _sdk.CRC(cmd)
        cmd_pub.Write(cmd)
        time.sleep(0.002)
    
    for _ in range(250):
        cmd = _sdk.unitree_go_msg_dds__LowCmd_()
        for i in range(12):
            cmd.motor_cmd[i].mode = 1
            cmd.motor_cmd[i].q = float(STAND_Q[i])
            cmd.motor_cmd[i].dq = 0.0
            cmd.motor_cmd[i].kp = float(STAND_KP)
            cmd.motor_cmd[i].kd = float(STAND_KD)
            cmd.motor_cmd[i].tau = 0.0
        for i in range(12, 20):
            cmd.motor_cmd[i].mode = 1
            cmd.motor_cmd[i].q = 2146000000.0
            cmd.motor_cmd[i].dq = 16000.0
            cmd.motor_cmd[i].kp = 0.0
            cmd.motor_cmd[i].kd = 0.0
            cmd.motor_cmd[i].tau = 0.0
        cmd.crc = _sdk.CRC(cmd)
        cmd_pub.Write(cmd)
        time.sleep(0.002)
    return {}

def capability_cap_sit_down(*, _sdk):
    SIT_Q = [0.0, 1.3, -2.6, 0.0, 1.3, -2.6, 0.0, 1.3, -2.6, 0.0, 1.3, -2.6]
    STAND_KP = 60.0
    STAND_KD = 3.0
    
    factory_init = _sdk.ChannelFactoryInitialize()
    cmd_pub = _sdk.ChannelPublisher('rt/lowcmd', _sdk.LowCmd_)
    cmd_pub.Init()
    state_sub = _sdk.ChannelSubscriber('rt/lowstate', _sdk.LowState_)
    state_sub.Init()
    
    start_time = time.time()
    state = state_sub.Read()
    q_start = [state.motor_state[i].q for i in range(12)]
    duration = 2.0
    
    while time.time() - start_time < duration:
        alpha = (time.time() - start_time) / duration
        alpha = min(1.0, alpha)
        q_des = [q_start[i] + alpha * (SIT_Q[i] - q_start[i]) for i in range(12)]
        
        cmd = _sdk.unitree_go_msg_dds__LowCmd_()
        for i in range(12):
            cmd.motor_cmd[i].mode = 1
            cmd.motor_cmd[i].q = float(q_des[i])
            cmd.motor_cmd[i].dq = 0.0
            cmd.motor_cmd[i].kp = float(STAND_KP)
            cmd.motor_cmd[i].kd = float(STAND_KD)
            cmd.motor_cmd[i].tau = 0.0
        for i in range(12, 20):
            cmd.motor_cmd[i].mode = 1
            cmd.motor_cmd[i].q = 2146000000.0
            cmd.motor_cmd[i].dq = 16000.0
            cmd.motor_cmd[i].kp = 0.0
            cmd.motor_cmd[i].kd = 0.0
            cmd.motor_cmd[i].tau = 0.0
        cmd.crc = _sdk.CRC(cmd)
        cmd_pub.Write(cmd)
        time.sleep(0.002)
    
    for _ in range(250):
        cmd = _sdk.unitree_go_msg_dds__LowCmd_()
        for i in range(12):
            cmd.motor_cmd[i].mode = 1
            cmd.motor_cmd[i].q = float(SIT_Q[i])
            cmd.motor_cmd[i].dq = 0.0
            cmd.motor_cmd[i].kp = float(STAND_KP)
            cmd.motor_cmd[i].kd = float(STAND_KD)
            cmd.motor_cmd[i].tau = 0.0
        for i in range(12, 20):
            cmd.motor_cmd[i].mode = 1
            cmd.motor_cmd[i].q = 2146000000.0
            cmd.motor_cmd[i].dq = 16000.0
            cmd.motor_cmd[i].kp = 0.0
            cmd.motor_cmd[i].kd = 0.0
            cmd.motor_cmd[i].tau = 0.0
        cmd.crc = _sdk.CRC(cmd)
        cmd_pub.Write(cmd)
        time.sleep(0.002)
    return {}

def capability_cap_hold_stance(arg_duration, *, _sdk):
    STAND_Q = [0.0, 0.9, -1.8, 0.0, 0.9, -1.8, 0.0, 0.9, -1.8, 0.0, 0.9, -1.8]
    STAND_KP = 60.0
    STAND_KD = 3.0
    
    factory_init = _sdk.ChannelFactoryInitialize()
    cmd_pub = _sdk.ChannelPublisher('rt/lowcmd', _sdk.LowCmd_)
    cmd_pub.Init()
    
    start_time = time.time()
    while time.time() - start_time < arg_duration:
        cmd = _sdk.unitree_go_msg_dds__LowCmd_()
        for i in range(12):
            cmd.motor_cmd[i].mode = 1
            cmd.motor_cmd[i].q = float(STAND_Q[i])
            cmd.motor_cmd[i].dq = 0.0
            cmd.motor_cmd[i].kp = float(STAND_KP)
            cmd.motor_cmd[i].kd = float(STAND_KD)
            cmd.motor_cmd[i].tau = 0.0
        for i in range(12, 20):
            cmd.motor_cmd[i].mode = 1
            cmd.motor_cmd[i].q = 2146000000.0
            cmd.motor_cmd[i].dq = 16000.0
            cmd.motor_cmd[i].kp = 0.0
            cmd.motor_cmd[i].kd = 0.0
            cmd.motor_cmd[i].tau = 0.0
        cmd.crc = _sdk.CRC(cmd)
        cmd_pub.Write(cmd)
        time.sleep(0.002)
    return {}

def capability_cap_move_forward(arg_distance, *, _sdk):
    STAND_Q = [0.0, 0.9, -1.8, 0.0, 0.9, -1.8, 0.0, 0.9, -1.8, 0.0, 0.9, -1.8]
    STAND_KP = 60.0
    STAND_KD = 3.0
    MOVE_KP = 40.0
    MOVE_KD = 2.5
    
    factory_init = _sdk.ChannelFactoryInitialize()
    cmd_pub = _sdk.ChannelPublisher('rt/lowcmd', _sdk.LowCmd_)
    cmd_pub.Init()
    sport_sub = _sdk.ChannelSubscriber('rt/sportmodestate', _sdk.SportModeState_)
    sport_sub.Init()
    
    sport_sub.Read()
    time.sleep(0.05)
    sport_state = sport_sub.Read()
    start_pos = sport_state.position[0]
    cycle_time = 0.5
    
    while True:
        sport_state = sport_sub.Read()
        current_pos = sport_state.position[0]
        if current_pos - start_pos >= arg_distance:
            break
        t = time.time() % cycle_time
        phase = t / cycle_time
        q_offset = [0.0] * 12
        if phase < 0.5:
            swing_phase = phase * 2.0
            q_offset[0] = 0.1 * math.sin(swing_phase * math.pi)
            q_offset[1] = -0.2 * math.sin(swing_phase * math.pi)
            q_offset[2] = 0.3 * math.sin(swing_phase * math.pi)
        else:
            swing_phase = (phase - 0.5) * 2.0
            q_offset[3] = 0.1 * math.sin(swing_phase * math.pi)
            q_offset[4] = -0.2 * math.sin(swing_phase * math.pi)
            q_offset[5] = 0.3 * math.sin(swing_phase * math.pi)
        q_des = [STAND_Q[i] + q_offset[i] for i in range(12)]
        
        cmd = _sdk.unitree_go_msg_dds__LowCmd_()
        for i in range(12):
            cmd.motor_cmd[i].mode = 1
            cmd.motor_cmd[i].q = float(q_des[i])
            cmd.motor_cmd[i].dq = 0.0
            cmd.motor_cmd[i].kp = float(MOVE_KP)
            cmd.motor_cmd[i].kd = float(MOVE_KD)
            cmd.motor_cmd[i].tau = 0.0
        for i in range(12, 20):
            cmd.motor_cmd[i].mode = 1
            cmd.motor_cmd[i].q = 2146000000.0
            cmd.motor_cmd[i].dq = 16000.0
            cmd.motor_cmd[i].kp = 0.0
            cmd.motor_cmd[i].kd = 0.0
            cmd.motor_cmd[i].tau = 0.0
        cmd.crc = _sdk.CRC(cmd)
        cmd_pub.Write(cmd)
        time.sleep(0.002)
    
    for _ in range(250):
        cmd = _sdk.unitree_go_msg_dds__LowCmd_()
        for i in range(12):
            cmd.motor_cmd[i].mode = 1
            cmd.motor_cmd[i].q = float(STAND_Q[i])
            cmd.motor_cmd[i].dq = 0.0
            cmd.motor_cmd[i].kp = float(STAND_KP)
            cmd.motor_cmd[i].kd = float(STAND_KD)
            cmd.motor_cmd[i].tau = 0.0
        for i in range(12, 20):
            cmd.motor_cmd[i].mode = 1
            cmd.motor_cmd[i].q = 2146000000.0
            cmd.motor_cmd[i].dq = 16000.0
            cmd.motor_cmd[i].kp = 0.0
            cmd.motor_cmd[i].kd = 0.0
            cmd.motor_cmd[i].tau = 0.0
        cmd.crc = _sdk.CRC(cmd)
        cmd_pub.Write(cmd)
        time.sleep(0.002)
    return {}

def capability_cap_adjust_body_height(arg_target_height, *, _sdk):
    STAND_Q = [0.0, 0.9, -1.8, 0.0, 0.9, -1.8, 0.0, 0.9, -1.8, 0.0, 0.9, -1.8]
    STAND_KP = 60.0
    STAND_KD = 3.0
    
    factory_init = _sdk.ChannelFactoryInitialize()
    cmd_pub = _sdk.ChannelPublisher('rt/lowcmd', _sdk.LowCmd_)
    cmd_pub.Init()
    state_sub = _sdk.ChannelSubscriber('rt/lowstate', _sdk.LowState_)
    state_sub.Init()
    
    nominal_height = 0.28
    height_diff = arg_target_height - nominal_height
    leg_scale = -height_diff / 0.15
    leg_scale = max(-0.5, min(0.5, leg_scale))
    target_q = [STAND_Q[i] if i % 3 == 0 else STAND_Q[i] * (1.0 + leg_scale * 0.3) for i in range(12)]
    
    start_time = time.time()
    state = state_sub.Read()
    q_start = [state.motor_state[i].q for i in range(12)]
    duration = 1.5
    
    while time.time() - start_time < duration:
        alpha = (time.time() - start_time) / duration
        alpha = min(1.0, alpha)
        q_des = [q_start[i] + alpha * (target_q[i] - q_start[i]) for i in range(12)]
        
        cmd = _sdk.unitree_go_msg_dds__LowCmd_()
        for i in range(12):
            cmd.motor_cmd[i].mode = 1
            cmd.motor_cmd[i].q = float(q_des[i])
            cmd.motor_cmd[i].dq = 0.0
            cmd.motor_cmd[i].kp = float(STAND_KP)
            cmd.motor_cmd[i].kd = float(STAND_KD)
            cmd.motor_cmd[i].tau = 0.0
        for i in range(12, 20):
            cmd.motor_cmd[i].mode = 1
            cmd.motor_cmd[i].q = 2146000000.0
            cmd.motor_cmd[i].dq = 16000.0
            cmd.motor_cmd[i].kp = 0.0
            cmd.motor_cmd[i].kd = 0.0
            cmd.motor_cmd[i].tau = 0.0
        cmd.crc = _sdk.CRC(cmd)
        cmd_pub.Write(cmd)
        time.sleep(0.002)
    
    for _ in range(250):
        cmd = _sdk.unitree_go_msg_dds__LowCmd_()
        for i in range(12):
            cmd.motor_cmd[i].mode = 1
            cmd.motor_cmd[i].q = float(target_q[i])
            cmd.motor_cmd[i].dq = 0.0
            cmd.motor_cmd[i].kp = float(STAND_KP)
            cmd.motor_cmd[i].kd = float(STAND_KD)
            cmd.motor_cmd[i].tau = 0.0
        for i in range(12, 20):
            cmd.motor_cmd[i].mode = 1
            cmd.motor_cmd[i].q = 2146000000.0
            cmd.motor_cmd[i].dq = 16000.0
            cmd.motor_cmd[i].kp = 0.0
            cmd.motor_cmd[i].kd = 0.0
            cmd.motor_cmd[i].tau = 0.0
        cmd.crc = _sdk.CRC(cmd)
        cmd_pub.Write(cmd)
        time.sleep(0.002)
    return {}
