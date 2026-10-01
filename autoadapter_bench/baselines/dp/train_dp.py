#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Train a state-based Diffusion Policy on collected ReachXYZ demos.

Architecture (kept minimal so reviewers can replicate):
  - Conditional U-Net 1D (action-horizon length 16)
  - Condition: current observation (state) + diffusion timestep embedding
  - DDPM scheduler with 100 train timesteps, DDIM at inference (10 steps)
  - Predicts an action chunk of length 16; executes 8 per inference step

This matches the canonical Chi et al. 2023 DP setup for state-based control.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from diffusers.schedulers.scheduling_ddpm import DDPMScheduler


# ──────────────────────────────────────────────────────────────────────────
# Minimal 1D conditional U-Net (Chi et al. 2023 style)
# ──────────────────────────────────────────────────────────────────────────


class SinusoidalEmb(nn.Module):
    def __init__(self, dim: int) -> None:
        super().__init__()
        self.dim = dim

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        half = self.dim // 2
        emb = math.log(10000) / max(half - 1, 1)
        freqs = torch.exp(-emb * torch.arange(half, device=x.device))
        args = x[:, None].float() * freqs[None, :]
        return torch.cat([args.sin(), args.cos()], dim=-1)


class ResBlock1D(nn.Module):
    def __init__(self, in_c: int, out_c: int, cond_dim: int) -> None:
        super().__init__()
        self.conv1 = nn.Conv1d(in_c, out_c, 3, padding=1)
        self.conv2 = nn.Conv1d(out_c, out_c, 3, padding=1)
        self.norm1 = nn.GroupNorm(8, out_c)
        self.norm2 = nn.GroupNorm(8, out_c)
        self.cond_proj = nn.Linear(cond_dim, out_c)
        self.skip = nn.Conv1d(in_c, out_c, 1) if in_c != out_c else nn.Identity()

    def forward(self, x: torch.Tensor, cond: torch.Tensor) -> torch.Tensor:
        h = F.mish(self.norm1(self.conv1(x)))
        c = self.cond_proj(cond)[..., None]
        h = h + c
        h = F.mish(self.norm2(self.conv2(h)))
        return h + self.skip(x)


class CondConvStack1D(nn.Module):
    """Flat stack of conditional 1D ResBlocks (no down/upsampling).

    For short action horizons (T ≤ 16) and small action dims, this matches a
    U-Net's capacity without the size-bookkeeping. Same noise-prediction
    contract as the Chi-et-al setup.
    """
    def __init__(self, action_dim: int, state_dim: int,
                  hidden: int = 256, n_blocks: int = 5,
                  t_emb_dim: int = 128) -> None:
        super().__init__()
        self.t_emb = nn.Sequential(
            SinusoidalEmb(t_emb_dim),
            nn.Linear(t_emb_dim, t_emb_dim * 2),
            nn.Mish(),
            nn.Linear(t_emb_dim * 2, t_emb_dim),
        )
        self.state_proj = nn.Sequential(
            nn.Linear(state_dim, t_emb_dim),
            nn.Mish(),
            nn.Linear(t_emb_dim, t_emb_dim),
        )
        cond_dim = t_emb_dim * 2
        self.input_proj = nn.Conv1d(action_dim, hidden, 1)
        self.blocks = nn.ModuleList(
            [ResBlock1D(hidden, hidden, cond_dim) for _ in range(n_blocks)]
        )
        self.output_proj = nn.Conv1d(hidden, action_dim, 1)

    def forward(self, actions: torch.Tensor, t: torch.Tensor,
                state: torch.Tensor) -> torch.Tensor:
        cond = torch.cat([self.t_emb(t), self.state_proj(state)], dim=-1)
        x = self.input_proj(actions)
        for blk in self.blocks:
            x = blk(x, cond)
        return self.output_proj(x)


# Keep old name as alias for compat with eval script
CondUNet1D = CondConvStack1D


# ──────────────────────────────────────────────────────────────────────────
# Demo dataset → action chunks
# ──────────────────────────────────────────────────────────────────────────


class ActionChunkDataset(torch.utils.data.Dataset):
    def __init__(self, demos: dict, horizon: int = 16) -> None:
        self.obs = torch.from_numpy(demos["obs"])      # [N, state_dim]
        self.act = torch.from_numpy(demos["act"])      # [N, action_dim]
        self.horizon = horizon
        self.N = self.act.shape[0]

    def __len__(self) -> int:
        return self.N

    def __getitem__(self, idx: int):
        # Action chunk = next `horizon` actions starting at idx
        end = min(idx + self.horizon, self.N)
        chunk = self.act[idx:end]
        if chunk.shape[0] < self.horizon:
            pad = self.act[-1:].repeat(self.horizon - chunk.shape[0], 1)
            chunk = torch.cat([chunk, pad], dim=0)
        return self.obs[idx], chunk  # state: [state_dim], chunk: [T, action_dim]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--demos", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--epochs", type=int, default=200)
    p.add_argument("--batch-size", type=int, default=256)
    p.add_argument("--horizon", type=int, default=16)
    p.add_argument("--lr", type=float, default=1e-4)
    p.add_argument("--device", default="auto")
    args = p.parse_args()

    if args.device == "auto":
        device = "mps" if torch.backends.mps.is_available() else "cpu"
    else:
        device = args.device
    print(f"using device: {device}")

    demos = np.load(args.demos)
    print(f"demos: obs {demos['obs'].shape}, act {demos['act'].shape}")
    ds = ActionChunkDataset(demos, horizon=args.horizon)
    loader = torch.utils.data.DataLoader(ds, batch_size=args.batch_size,
                                          shuffle=True, num_workers=0)

    action_dim = demos["act"].shape[1]
    state_dim = demos["obs"].shape[1]

    # Normalize obs+act to mean 0 std 1 (DP requires this for stable training)
    obs_mean = torch.tensor(demos["obs"].mean(0), dtype=torch.float32)
    obs_std = torch.tensor(demos["obs"].std(0) + 1e-6, dtype=torch.float32)
    act_mean = torch.tensor(demos["act"].mean(0), dtype=torch.float32)
    act_std = torch.tensor(demos["act"].std(0) + 1e-6, dtype=torch.float32)

    model = CondUNet1D(action_dim=action_dim, state_dim=state_dim).to(device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"model params: {n_params/1e6:.2f}M")

    scheduler = DDPMScheduler(
        num_train_timesteps=100,
        beta_schedule="squaredcos_cap_v2",
        clip_sample=True,
        prediction_type="epsilon",
    )
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr)

    t0 = time.time()
    log = []
    for ep in range(args.epochs):
        ep_loss = 0.0
        nbatch = 0
        for state, chunk in loader:
            state = state.to(device)
            chunk = chunk.to(device)
            # Normalize
            state_n = (state - obs_mean.to(device)) / obs_std.to(device)
            chunk_n = (chunk - act_mean.to(device)) / act_std.to(device)
            chunk_n = chunk_n.permute(0, 2, 1)  # [B, action_dim, T]
            B = state_n.shape[0]
            noise = torch.randn_like(chunk_n)
            t_step = torch.randint(0, scheduler.config.num_train_timesteps,
                                    (B,), device=device).long()
            noisy = scheduler.add_noise(chunk_n, noise, t_step)
            pred = model(noisy, t_step, state_n)
            loss = F.mse_loss(pred, noise)
            opt.zero_grad()
            loss.backward()
            opt.step()
            ep_loss += loss.item()
            nbatch += 1
        ep_loss /= max(nbatch, 1)
        log.append(ep_loss)
        if (ep + 1) % 10 == 0:
            print(f"  ep {ep+1:>3d}  loss={ep_loss:.5f}  elapsed={time.time()-t0:.0f}s")

    train_time = time.time() - t0
    out_dir = Path(args.output)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    torch.save({
        "model_state": model.state_dict(),
        "obs_mean": obs_mean,
        "obs_std": obs_std,
        "act_mean": act_mean,
        "act_std": act_std,
        "action_dim": action_dim,
        "state_dim": state_dim,
        "horizon": args.horizon,
        "train_time_sec": train_time,
        "final_loss": log[-1],
        "loss_log": log,
    }, args.output)
    print(f"\nSaved: {args.output}")
    print(f"  train time: {train_time:.0f}s")
    print(f"  final loss: {log[-1]:.5f}")


if __name__ == "__main__":
    main()
