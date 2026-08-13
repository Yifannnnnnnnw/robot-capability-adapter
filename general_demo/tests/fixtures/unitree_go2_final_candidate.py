import math
import time


def capability_cap_stand_up(*, _sdk):
    STAND_Q = [0.0, 0.9, -1.8, 0.0, 0.9, -1.8, 0.0, 0.9, -1.8, 0.0, 0.9, -1.8]
    STAND_KP = 60.0
    STAND_KD = 3.0

    cmd_pub = _sdk.ChannelPublisher("rt/lowcmd", _sdk.LowCmd_)
    cmd_pub.Init()
    state_sub = _sdk.ChannelSubscriber("rt/lowstate", _sdk.LowState_)
    state_sub.Init()

    for cycle in range(200):
        state = state_sub.Read()
        if state is None:
            raise RuntimeError("lowstate unavailable")
        phase = min(1.0, cycle / 199.0)
        command = _sdk.unitree_go_msg_dds__LowCmd_()
        command.head[0] = 0xFE
        command.head[1] = 0xEF
        command.level_flag = 0xFF
        command.gpio = 0
        for i in range(12):
            fresh_q = state.motor_state[i].q
            phase_target = STAND_Q[i] * phase
            error = phase_target - float(fresh_q)
            desired = float(fresh_q) + max(-0.05, min(0.05, 0.25 * error))
            command.motor_cmd[i].mode = 1
            command.motor_cmd[i].q = fresh_q + (desired - float(fresh_q))
            command.motor_cmd[i].dq = 0.0
            command.motor_cmd[i].kp = float(STAND_KP)
            command.motor_cmd[i].kd = float(STAND_KD)
            command.motor_cmd[i].tau = 0.0
        for i in range(12, 20):
            command.motor_cmd[i].mode = 1
            command.motor_cmd[i].q = 2146000000.0
            command.motor_cmd[i].dq = 16000.0
            command.motor_cmd[i].kp = 0.0
            command.motor_cmd[i].kd = 0.0
            command.motor_cmd[i].tau = 0.0
        command.crc = _sdk.CRC().Crc(command)
        cmd_pub.Write(command)
        time.sleep(0.01)
    return {}


def capability_cap_sit_down(*, _sdk):
    SIT_Q = [0.0, 1.3, -2.6, 0.0, 1.3, -2.6, 0.0, 1.3, -2.6, 0.0, 1.3, -2.6]
    STAND_KP = 60.0
    STAND_KD = 3.0

    cmd_pub = _sdk.ChannelPublisher("rt/lowcmd", _sdk.LowCmd_)
    cmd_pub.Init()
    state_sub = _sdk.ChannelSubscriber("rt/lowstate", _sdk.LowState_)
    state_sub.Init()

    for cycle in range(200):
        state = state_sub.Read()
        if state is None:
            raise RuntimeError("lowstate unavailable")
        phase = min(1.0, cycle / 199.0)
        command = _sdk.unitree_go_msg_dds__LowCmd_()
        command.head[0] = 0xFE
        command.head[1] = 0xEF
        command.level_flag = 0xFF
        command.gpio = 0
        for i in range(12):
            fresh_q = state.motor_state[i].q
            phase_target = SIT_Q[i] * phase
            error = phase_target - float(fresh_q)
            desired = float(fresh_q) + max(-0.05, min(0.05, 0.25 * error))
            command.motor_cmd[i].mode = 1
            command.motor_cmd[i].q = fresh_q + (desired - float(fresh_q))
            command.motor_cmd[i].dq = 0.0
            command.motor_cmd[i].kp = float(STAND_KP)
            command.motor_cmd[i].kd = float(STAND_KD)
            command.motor_cmd[i].tau = 0.0
        for i in range(12, 20):
            command.motor_cmd[i].mode = 1
            command.motor_cmd[i].q = 2146000000.0
            command.motor_cmd[i].dq = 16000.0
            command.motor_cmd[i].kp = 0.0
            command.motor_cmd[i].kd = 0.0
            command.motor_cmd[i].tau = 0.0
        command.crc = _sdk.CRC().Crc(command)
        cmd_pub.Write(command)
        time.sleep(0.01)
    return {}


def capability_cap_hold_stance(arg_duration, *, _sdk):
    STAND_Q = [0.0, 0.9, -1.8, 0.0, 0.9, -1.8, 0.0, 0.9, -1.8, 0.0, 0.9, -1.8]
    STAND_KP = 60.0
    STAND_KD = 3.0
    cycles = max(2, min(1000, int(max(0.02, arg_duration) / 0.01)))

    cmd_pub = _sdk.ChannelPublisher("rt/lowcmd", _sdk.LowCmd_)
    cmd_pub.Init()
    state_sub = _sdk.ChannelSubscriber("rt/lowstate", _sdk.LowState_)
    state_sub.Init()

    for _ in range(cycles):
        state = state_sub.Read()
        if state is None:
            raise RuntimeError("lowstate unavailable")
        command = _sdk.unitree_go_msg_dds__LowCmd_()
        command.head[0] = 0xFE
        command.head[1] = 0xEF
        command.level_flag = 0xFF
        command.gpio = 0
        for i in range(12):
            fresh_q = state.motor_state[i].q
            fresh_dq = state.motor_state[i].dq
            desired = float(fresh_q) - 0.02 * float(fresh_dq)
            command.motor_cmd[i].mode = 1
            command.motor_cmd[i].q = fresh_q + (desired - float(fresh_q))
            command.motor_cmd[i].dq = fresh_dq
            command.motor_cmd[i].kp = float(STAND_KP)
            command.motor_cmd[i].kd = float(STAND_KD)
            command.motor_cmd[i].tau = -0.1 * float(fresh_dq)
        for i in range(12, 20):
            command.motor_cmd[i].mode = 1
            command.motor_cmd[i].q = 2146000000.0
            command.motor_cmd[i].dq = 16000.0
            command.motor_cmd[i].kp = 0.0
            command.motor_cmd[i].kd = 0.0
            command.motor_cmd[i].tau = 0.0
        command.crc = _sdk.CRC().Crc(command)
        cmd_pub.Write(command)
        time.sleep(0.01)
    return {}


def capability_cap_move_forward(arg_distance, *, _sdk):
    STAND_Q = [0.0, 0.9, -1.8, 0.0, 0.9, -1.8, 0.0, 0.9, -1.8, 0.0, 0.9, -1.8]
    MOVE_KP = 40.0
    MOVE_KD = 2.5
    cycles = max(2, min(500, int(max(0.02, abs(arg_distance)) / 0.01)))
    amplitude = max(-0.2, min(0.2, arg_distance))

    cmd_pub = _sdk.ChannelPublisher("rt/lowcmd", _sdk.LowCmd_)
    cmd_pub.Init()
    state_sub = _sdk.ChannelSubscriber("rt/lowstate", _sdk.LowState_)
    state_sub.Init()

    for cycle in range(cycles):
        state = state_sub.Read()
        if state is None:
            raise RuntimeError("lowstate unavailable")
        phase = 2.0 * math.pi * cycle / cycles
        q_offset = [0.0] * 12
        q_offset[0] = amplitude * 0.5 * math.sin(phase)
        q_offset[1] = -amplitude * math.sin(phase)
        q_offset[2] = amplitude * 1.5 * math.sin(phase)
        q_offset[3] = amplitude * 0.5 * math.sin(phase + math.pi)
        q_offset[4] = -amplitude * math.sin(phase + math.pi)
        q_offset[5] = amplitude * 1.5 * math.sin(phase + math.pi)
        command = _sdk.unitree_go_msg_dds__LowCmd_()
        command.head[0] = 0xFE
        command.head[1] = 0xEF
        command.level_flag = 0xFF
        command.gpio = 0
        for i in range(12):
            fresh_q = state.motor_state[i].q
            phase_target = STAND_Q[i] + q_offset[i]
            error = phase_target - float(fresh_q)
            desired = float(fresh_q) + max(-0.05, min(0.05, 0.25 * error))
            command.motor_cmd[i].mode = 1
            command.motor_cmd[i].q = fresh_q + (desired - float(fresh_q))
            command.motor_cmd[i].dq = 0.0
            command.motor_cmd[i].kp = float(MOVE_KP)
            command.motor_cmd[i].kd = float(MOVE_KD)
            command.motor_cmd[i].tau = 0.0
        for i in range(12, 20):
            command.motor_cmd[i].mode = 1
            command.motor_cmd[i].q = 2146000000.0
            command.motor_cmd[i].dq = 16000.0
            command.motor_cmd[i].kp = 0.0
            command.motor_cmd[i].kd = 0.0
            command.motor_cmd[i].tau = 0.0
        command.crc = _sdk.CRC().Crc(command)
        cmd_pub.Write(command)
        time.sleep(0.01)
    return {}


def capability_cap_adjust_body_height(arg_target_height, *, _sdk):
    STAND_Q = [0.0, 0.9, -1.8, 0.0, 0.9, -1.8, 0.0, 0.9, -1.8, 0.0, 0.9, -1.8]
    STAND_KP = 60.0
    STAND_KD = 3.0
    height_diff = arg_target_height - 0.28
    leg_scale = max(-0.5, min(0.5, -height_diff / 0.15))
    target_q = [
        STAND_Q[i] if i % 3 == 0 else STAND_Q[i] * (1.0 + leg_scale * 0.3)
        for i in range(12)
    ]

    cmd_pub = _sdk.ChannelPublisher("rt/lowcmd", _sdk.LowCmd_)
    cmd_pub.Init()
    state_sub = _sdk.ChannelSubscriber("rt/lowstate", _sdk.LowState_)
    state_sub.Init()

    for cycle in range(150):
        state = state_sub.Read()
        if state is None:
            raise RuntimeError("lowstate unavailable")
        phase = min(1.0, cycle / 149.0)
        command = _sdk.unitree_go_msg_dds__LowCmd_()
        command.head[0] = 0xFE
        command.head[1] = 0xEF
        command.level_flag = 0xFF
        command.gpio = 0
        for i in range(12):
            fresh_q = state.motor_state[i].q
            phase_target = target_q[i] * phase
            error = phase_target - float(fresh_q)
            desired = float(fresh_q) + max(-0.05, min(0.05, 0.25 * error))
            command.motor_cmd[i].mode = 1
            command.motor_cmd[i].q = fresh_q + (desired - float(fresh_q))
            command.motor_cmd[i].dq = 0.0
            command.motor_cmd[i].kp = float(STAND_KP)
            command.motor_cmd[i].kd = float(STAND_KD)
            command.motor_cmd[i].tau = 0.0
        for i in range(12, 20):
            command.motor_cmd[i].mode = 1
            command.motor_cmd[i].q = 2146000000.0
            command.motor_cmd[i].dq = 16000.0
            command.motor_cmd[i].kp = 0.0
            command.motor_cmd[i].kd = 0.0
            command.motor_cmd[i].tau = 0.0
        command.crc = _sdk.CRC().Crc(command)
        cmd_pub.Write(command)
        time.sleep(0.01)
    return {}
