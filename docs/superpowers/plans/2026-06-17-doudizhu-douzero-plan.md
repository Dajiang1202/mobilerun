# DouZero 斗地主集成 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 DouZero AI 决策引擎集成到 gameauto 框架，新建独立 `skills/doudizhu_douzero/` skill，CV 模板匹配感知 + DeepAgent 决策 + HDC 执行。

**Architecture:** 搬运 DouZero `env/` + `models.py` + `deep_agent.py` 到 skill 内，最小修改解耦；新建 gameauto 标准胶水层（perception / decision / states / skill / run script）。

**Tech Stack:** Python 3, PyTorch, OpenCV (cv2), numpy, gameauto core framework

---

## 文件清单

```
Create:
  skills/doudizhu_douzero/__init__.py
  skills/doudizhu_douzero/config.yaml
  skills/doudizhu_douzero/douzero/__init__.py
  skills/doudizhu_douzero/douzero/env/__init__.py
  skills/doudizhu_douzero/douzero/env/utils.py        (copy, no changes)
  skills/doudizhu_douzero/douzero/env/move_detector.py (copy, fix import)
  skills/doudizhu_douzero/douzero/env/move_generator.py(copy, fix import)
  skills/doudizhu_douzero/douzero/env/move_selector.py (copy, fix import)
  skills/doudizhu_douzero/douzero/env/game.py          (copy + adapt)
  skills/doudizhu_douzero/douzero/env/env.py           (copy + adapt)
  skills/doudizhu_douzero/douzero/models.py            (copy + adapt)
  skills/doudizhu_douzero/douzero/deep_agent.py        (copy + adapt)
  skills/doudizhu_douzero/douzero/baselines/            (copy weights)
  skills/doudizhu_douzero/perception.py
  skills/doudizhu_douzero/decision.py
  skills/doudizhu_douzero/visualizer.py
  skills/doudizhu_douzero/states.py
  skills/doudizhu_douzero/skill.py
  skills/doudizhu_douzero/assets/templates/.gitkeep
  gameauto/run_doudizhu_douzero.py

Modify: (none — all new files)
```

---

### Task 1: 创建目录结构 + 搬运无依赖工具文件

**Files:**
- Create: `skills/doudizhu_douzero/__init__.py`
- Create: `skills/doudizhu_douzero/douzero/__init__.py`
- Create: `skills/doudizhu_douzero/douzero/env/__init__.py`
- Create: `skills/doudizhu_douzero/douzero/env/utils.py`
- Create: `skills/doudizhu_douzero/douzero/baselines/.gitkeep`

- [ ] **Step 1: 创建目录结构**

```bash
mkdir -p gameauto/skills/doudizhu_douzero/douzero/env
mkdir -p gameauto/skills/doudizhu_douzero/douzero/baselines
mkdir -p gameauto/skills/doudizhu_douzero/assets/templates
```

- [ ] **Step 2: 创建空的 __init__.py**

创建 `gameauto/skills/doudizhu_douzero/__init__.py`，内容为空。

创建 `gameauto/skills/doudizhu_douzero/douzero/__init__.py`，内容为空。

- [ ] **Step 3: 创建 `douzero/env/__init__.py`** — 导出 GameEnv 和 InfoSet

```python
from .game import GameEnv, InfoSet

__all__ = ["GameEnv", "InfoSet"]
```

- [ ] **Step 4: 搬运 `utils.py`** — 原样复制

从 `tmp/DouZero_For_HappyDouDiZhu-2.0/douzero/env/utils.py` 复制到 `skills/doudizhu_douzero/douzero/env/utils.py`，不改任何代码。

- [ ] **Step 5: 搬运 `move_detector.py`** — 修改 import

从 `tmp/DouZero_For_HappyDouDiZhu-2.0/douzero/env/move_detector.py` 复制到 `skills/doudizhu_douzero/douzero/env/move_detector.py`。

将文件中的：
```python
from douzero.env.utils import *
```
替换为：
```python
from .utils import *
```

- [ ] **Step 6: 搬运 `move_generator.py`** — 修改 import

从 `tmp/DouZero_For_HappyDouDiZhu-2.0/douzero/env/move_generator.py` 复制到 `skills/doudizhu_douzero/douzero/env/move_generator.py`。

将文件中的：
```python
from douzero.env.utils import *
from douzero.env.move_detector import *
```
替换为：
```python
from .utils import *
from .move_detector import *
```

- [ ] **Step 7: 搬运 `move_selector.py`** — 修改 import

从 `tmp/DouZero_For_HappyDouDiZhu-2.0/douzero/env/move_selector.py` 复制到 `skills/doudizhu_douzero/douzero/env/move_selector.py`。

将文件中的：
```python
from douzero.env.utils import *
```
替换为：
```python
from .utils import *
```

- [ ] **Step 8: 创建 baselines 占位 + 复制模型权重**

```bash
cp -r tmp/DouZero_For_HappyDouDiZhu-2.0/baselines/douzero_WP/*.ckpt gameauto/skills/doudizhu_douzero/douzero/baselines/
```

验证：
```bash
ls gameauto/skills/doudizhu_douzero/douzero/baselines/
# 应看到: landlord.ckpt  landlord_up.ckpt  landlord_down.ckpt
```

- [ ] **Step 9: 验证 import 链**

```bash
cd gameauto && python -c "from skills.doudizhu_douzero.douzero.env.utils import TYPE_0_PASS; print('utils OK:', TYPE_0_PASS)"
python -c "from skills.doudizhu_douzero.douzero.env.move_detector import get_move_type; print('move_detector OK')"
python -c "from skills.doudizhu_douzero.douzero.env.move_generator import MovesGener; print('move_generator OK')"
python -c "from skills.doudizhu_douzero.douzero.env.move_selector import filter_type_1_single; print('move_selector OK')"
```

- [ ] **Step 10: Commit**

```bash
git add gameauto/skills/doudizhu_douzero/
git commit -m "feat(doudizhu_douzero): Task 1 — 目录结构 + env 工具模块搬运"
```

---

### Task 2: 搬运并适配 game.py（GameEnv + InfoSet）

**Files:**
- Create: `skills/doudizhu_douzero/douzero/env/game.py`

- [ ] **Step 1: 复制 game.py**

```bash
cp tmp/DouZero_For_HappyDouDiZhu-2.0/douzero/env/game.py gameauto/skills/doudizhu_douzero/douzero/env/game.py
```

- [ ] **Step 2: 修改 `GameEnv.__init__` — 接受 dict 而非 list**

将 `__init__(self, players)` 中的 `players` 参数语义改为 dict，同时支持 `DummyAgent`:

```python
def __init__(self, players):
    # players: dict[str, DummyAgent] keyed by position
    # Each agent implements act(infoset) -> (action, confidence)
    
    self.card_play_action_seq = []
    self.three_landlord_cards = None
    self.game_over = False
    self.acting_player_position = None
    self.player_utility_dict = None
    self.players = players  # dict
    
    # ... (rest of __init__ unchanged)
```

- [ ] **Step 3: 修改 `GameEnv.step` — 使用 dict-based players**

将 `step()` 方法中的硬编码 `self.players[1].act()` 改为从 dict 取当前玩家:

```python
def step(self):
    """Execute one step. Action is sourced from the acting player's agent via act()."""
    # Get action from the acting player's agent
    agent = self.players[self.acting_player_position]
    action, actions_confidence = agent.act(self.game_infoset)
    
    # Calculate win rate
    win_rate = max(actions_confidence, -1)
    win_rate = min(win_rate, 1)
    win_rate = str(round(float((win_rate + 1) / 2), 4))
    
    if len(action) > 0:
        self.last_pid = self.acting_player_position
    
    if action in bombs:
        self.bomb_num += 1
    
    self.last_move_dict[self.acting_player_position] = action.copy()
    self.card_play_action_seq.append(action)
    self.update_acting_player_hand_cards(action)
    self.played_cards[self.acting_player_position] += action
    
    if self.acting_player_position == 'landlord' and \
            len(action) > 0 and \
            len(self.three_landlord_cards) > 0:
        for card in action:
            if len(self.three_landlord_cards) > 0:
                if card in self.three_landlord_cards:
                    self.three_landlord_cards.remove(card)
            else:
                break
    
    self.game_done()
    if not self.game_over:
        self.get_acting_player_position()
        self.game_infoset = self.get_infoset()
    
    action_message = {
        "action": str(''.join([EnvCard2RealCard[c] for c in action])),
        "win_rate": str(round(float(win_rate) * 100, 2)) + "%"
    }
    return action_message
```

即：移除 `position` 和 `action` 参数，移除 `if self.acting_player_position == position` 判断，始终从 `self.players[self.acting_player_position].act()` 取动作。

- [ ] **Step 4: 修改 `update_acting_player_hand_cards` — 移除 `players[0]` 判断**

将：
```python
if self.acting_player_position == self.players[0]:
```
改为：
```python
if self.acting_player_position == 'landlord':
    # For landlord, remove specific cards (we know exact hand)
    ...
else:
    # For farmers, remove from front (we only know count from CV)
    ...
```

Wait, actually for our use case, we know our own hand cards exactly. For opponents, we only know what they played. Let me keep it simple — use the "remove from front" approach for ALL positions since we always know exactly which cards we're removing:

```python
def update_acting_player_hand_cards(self, action):
    if action != []:
        for card in action:
            self.info_sets[self.acting_player_position].player_hand_cards.remove(card)
        self.info_sets[self.acting_player_position].player_hand_cards.sort()
```

This removes the `players[0]` dependency entirely.

- [ ] **Step 5: `game.py` 中移除对 `deep_agent` 的 import**

确认 `game.py` 中没有 `from douzero.evaluation...` 的 import。如果有，删除。GameEnv 不依赖 DeepAgent。

- [ ] **Step 6: 验证 GameEnv 基本功能**

```bash
cd gameauto && python -c "
from skills.doudizhu_douzero.douzero.env.game import GameEnv, InfoSet, EnvCard2RealCard, RealCard2EnvCard

# Test basic init
class FakeAgent:
    def __init__(self, pos): self.pos = pos
    def act(self, infoset): return ([], 0)

agents = {pos: FakeAgent(pos) for pos in ['landlord', 'landlord_up', 'landlord_down']}
env = GameEnv(agents)
print('GameEnv init OK')

# Test card_play_init
data = {
    'landlord': [3,3,3,4,5,5,6,7,8,9,10,11,12,13,14,17,17,20,20,30],
    'landlord_up': [3,4,4,5,6,7,8,9,10,11,12,13,14,14,17,17,30],
    'landlord_down': [4,5,6,7,8,9,10,11,12,13,14,14,17,17,17,20,30],
    'three_landlord_cards': [3,4,5],
}
env.card_play_init(data)
print('card_play_init OK, acting:', env.acting_player_position)

# Test step
env.step()
print('step OK, game_over:', env.game_over)
print('All GameEnv tests passed!')
"
```

- [ ] **Step 7: Commit**

```bash
git add gameauto/skills/doudizhu_douzero/douzero/env/game.py
git commit -m "feat(doudizhu_douzero): Task 2 — 适配 GameEnv + InfoSet"
```

---

### Task 3: 搬运并适配 env.py（观测编码 + DummyAgent）

**Files:**
- Create: `skills/doudizhu_douzero/douzero/env/env.py`

- [ ] **Step 1: 复制 env.py**

```bash
cp tmp/DouZero_For_HappyDouDiZhu-2.0/douzero/env/env.py gameauto/skills/doudizhu_douzero/douzero/env/env.py
```

- [ ] **Step 2: 修改 import 路径**

将：
```python
from douzero.env.game import GameEnv
```
替换为：
```python
from .game import GameEnv
```

- [ ] **Step 3: 修改 `DummyAgent.act` — 返回 tuple 兼容 step()**

将其：
```python
def act(self, infoset):
    assert self.action in infoset.legal_actions
    return self.action
```
修改为：
```python
def act(self, infoset):
    """Return (action, confidence) tuple for GameEnv.step() compatibility."""
    assert self.action in infoset.legal_actions
    return self.action, 0  # confidence=0 for dummy
```

- [ ] **Step 4: 修改 `Env.__init__` — 转发 players dict 给 GameEnv**

确认 `Env.__init__` 将 `self.players` dict 传给 `GameEnv(self.players)`，确保 GameEnv 接收的是 dict。

- [ ] **Step 5: 修改 `Env.step` — 调用 `self._env.step()`**

确认 `Env.step(action)` 中的流程：
1. `self.players[self._acting_player_position].set_action(action)` — 设置 DummyAgent 动作
2. `self._env.step()` — 调用 GameEnv.step()（无参数版）

- [ ] **Step 6: 添加 `map_location='cpu'` 兼容代码**

在 `Env` 初始化时确保所有 tensor 操作默认在 CPU 上。env.py 本身只做 numpy 编码，不涉及 torch，确认无需修改。

- [ ] **Step 7: 验证 env.py**

```bash
cd gameauto && python -c "
from skills.doudizhu_douzero.douzero.env.env import Env, DummyAgent, get_obs

# Test DummyAgent
agent = DummyAgent('landlord')
agent.set_action([])
action, conf = agent.act(None)
assert action == [] and conf == 0
print('DummyAgent OK')

# Test Env
env = Env(objective='wp')
obs = env.reset()
print('Env reset OK, obs keys:', obs.keys())
print('legal_actions count:', len(obs['legal_actions']))
print('position:', obs['position'])

# Test one step
legal = obs['legal_actions']
action = legal[0]  # take first legal action
obs, reward, done, _ = env.step(action)
print('step OK, reward:', reward, 'done:', done)
print('All env.py tests passed!')
"
```

- [ ] **Step 8: Commit**

```bash
git add gameauto/skills/doudizhu_douzero/douzero/env/env.py
git commit -m "feat(doudizhu_douzero): Task 3 — 适配 env.py 观测编码 + DummyAgent"
```

---

### Task 4: 搬运并适配 models.py

**Files:**
- Create: `skills/doudizhu_douzero/douzero/models.py`

- [ ] **Step 1: 复制 models.py**

```bash
cp tmp/DouZero_For_HappyDouDiZhu-2.0/douzero/dmc/models.py gameauto/skills/doudizhu_douzero/douzero/models.py
```

- [ ] **Step 2: 添加 `map_location='cpu'` fallback**

Model 类在第 92-96 行强制使用 `torch.device('cuda:0')`，需要改为 CPU-friendly:

```python
class Model:
    def __init__(self, device='cpu'):
        self.models = {}
        device = torch.device(device)
        self.models['landlord'] = LandlordLstmModel().to(device)
        self.models['landlord_up'] = FarmerLstmModel().to(device)
        self.models['landlord_down'] = FarmerLstmModel().to(device)
```

即：`device` 参数默认改为 `'cpu'`，使用 `torch.device()` 包装以支持字符串。

- [ ] **Step 3: 验证模型加载**

```bash
cd gameauto && python -c "
import torch
from skills.doudizhu_douzero.douzero.models import LandlordLstmModel, FarmerLstmModel, model_dict

# Test instantiation
landlord = LandlordLstmModel()
farmer = FarmerLstmModel()
print('Landlord params:', sum(p.numel() for p in landlord.parameters()))
print('Farmer params:', sum(p.numel() for p in farmer.parameters()))

# Test forward pass
import numpy as np
z = torch.randn(1, 5, 162)
x_landlord = torch.randn(1, 373)
x_farmer = torch.randn(1, 484)

with torch.no_grad():
    out_l = landlord(z, x_landlord, return_value=True)
    out_f = farmer(z, x_farmer, return_value=True)
print('Landlord output:', out_l['values'].shape)
print('Farmer output:', out_f['values'].shape)
print('All models.py tests passed!')
"
```

- [ ] **Step 4: Commit**

```bash
git add gameauto/skills/doudizhu_douzero/douzero/models.py
git commit -m "feat(doudizhu_douzero): Task 4 — 适配 models.py (CPU default)"
```

---

### Task 5: 搬运并适配 deep_agent.py

**Files:**
- Create: `skills/doudizhu_douzero/douzero/deep_agent.py`

- [ ] **Step 1: 复制 deep_agent.py**

从 `tmp/DouZero_For_HappyDouDiZhu-2.0/douzero/evaluation/deep_agent.py` 复制到 `skills/doudizhu_douzero/douzero/deep_agent.py`

- [ ] **Step 2: 修改 import 路径**

将：
```python
from douzero.env.env import get_obs
...
from douzero.dmc.models import model_dict
```
替换为：
```python
from .env.env import get_obs
from .models import model_dict
```

- [ ] **Step 3: 修改 `_load_model` — 默认 CPU**

将：
```python
def _load_model(position, model_path):
    from douzero.dmc.models import model_dict
    model = model_dict[position]()
    model_state_dict = model.state_dict()
    if torch.cuda.is_available():
        pretrained = torch.load(model_path, map_location='cuda:0')
    else:
        pretrained = torch.load(model_path, map_location='cpu')
    pretrained = {k: v for k, v in pretrained.items() if k in model_state_dict}
    model_state_dict.update(pretrained)
    model.load_state_dict(model_state_dict)
    if torch.cuda.is_available():
        model.cuda()
    model.eval()
    return model
```
改为：
```python
def _load_model(position, model_path):
    model = model_dict[position]()
    model_state_dict = model.state_dict()
    pretrained = torch.load(model_path, map_location='cpu')
    pretrained = {k: v for k, v in pretrained.items() if k in model_state_dict}
    model_state_dict.update(pretrained)
    model.load_state_dict(model_state_dict)
    model.eval()
    return model
```

- [ ] **Step 4: 修改 `DeepAgent.act` — 移除 CUDA 分支**

将 act() 中的 `.cuda()` 调用移除，始终在 CPU 上运行：

```python
def act(self, infoset):
    obs = get_obs(infoset)
    z_batch = torch.from_numpy(obs['z_batch']).float()
    x_batch = torch.from_numpy(obs['x_batch']).float()
    y_pred = self.model.forward(z_batch, x_batch, return_value=True)['values']
    y_pred = y_pred.detach().cpu().numpy()
    
    best_action_index = np.argmax(y_pred, axis=0)[0]
    best_action = infoset.legal_actions[best_action_index]
    best_action_confidence = y_pred[best_action_index]
    return best_action, best_action_confidence
```

- [ ] **Step 5: 验证 DeepAgent 加载真实模型**

```bash
cd gameauto && python -c "
import sys
sys.path.insert(0, '.')
from skills.doudizhu_douzero.douzero.deep_agent import DeepAgent
from skills.doudizhu_douzero.douzero.env.env import Env

# Load model and test on a game state
agent = DeepAgent('landlord', 'gameauto/skills/doudizhu_douzero/douzero/baselines/landlord.ckpt')
print('DeepAgent loaded OK')

env = Env(objective='wp')
obs = env.reset()

# Get infoset and act
infoset = env.infoset
action, confidence = agent.act(infoset)
print('Action:', action)
print('Confidence:', confidence)
print('All deep_agent.py tests passed!')
"
```

- [ ] **Step 6: Commit**

```bash
git add gameauto/skills/doudizhu_douzero/douzero/deep_agent.py
git commit -m "feat(doudizhu_douzero): Task 5 — 适配 deep_agent.py (CPU-only)"
```

---

### Task 6: 创建 config.yaml

**Files:**
- Create: `skills/doudizhu_douzero/config.yaml`

- [ ] **Step 1: 写入 config.yaml**

```yaml
# DouZero 斗地主配置
# 对局设置
max_rounds: 20

# DouZero 模型路径（相对于 skill 目录）
model_dir: douzero/baselines

# 模板匹配置信度
template_confidence: 0.90
card_confidence: 0.85
pass_confidence: 0.90
button_confidence: 0.90

# 时间控制（秒）
wait_between_rounds: 3.0
wait_after_action: 1.5
tap_interval: 0.08
wait_for_opponent: 2.0
```

- [ ] **Step 2: 验证配置可加载**

```bash
cd gameauto && python -c "
from gameauto.config.loader import load_game_config
cfg = load_game_config('doudizhu_douzero')
print('Config loaded:', cfg)
assert cfg['max_rounds'] == 20
print('Config OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add gameauto/skills/doudizhu_douzero/config.yaml
git commit -m "feat(doudizhu_douzero): Task 6 — config.yaml"
```

---

### Task 7: 创建 perception.py（CV 模板匹配感知）

**Files:**
- Create: `skills/doudizhu_douzero/perception.py`

- [ ] **Step 1: 写入 perception.py**

```python
"""DouDiZhu DouZero CV perception — template matching for cards, buttons, UI.

Uses PerceptionPipeline with parallel TemplateMatchTask instances.
No VLM dependency — all recognition is OpenCV-based.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from gameauto.core.perception.base import PerceptionResult, PerceptionTask
from gameauto.core.perception.cv.template_match import TemplateMatchTask
from gameauto.core.perception.pipeline import PerceptionPipeline

logger = logging.getLogger("gameauto.doudizhu_douzero")


class DouDiZhuDouzeroPerception:
    """CV-based perception for DouDiZhu using template matching.

    Detects: hand cards, opponent plays, landlord markers, 
    bidding buttons (叫地主/不叫/抢地主/加倍), play buttons (出牌/不出/提示),
    settlement buttons (继续).

    Usage:
        perception = DouDiZhuDouzeroPerception(template_dir="skills/doudizhu_douzero/assets/templates")
        result = await perception.recognize(image_bytes)
    """

    def __init__(
        self,
        template_dir: str,
        card_confidence: float = 0.85,
        button_confidence: float = 0.90,
        pass_confidence: float = 0.90,
    ) -> None:
        self._template_dir = Path(template_dir)
        self._card_confidence = card_confidence
        self._button_confidence = button_confidence
        self._pass_confidence = pass_confidence

        # Template matchers for different regions
        self._card_matcher: TemplateMatchTask | None = None
        self._other_card_matcher: TemplateMatchTask | None = None
        self._button_matcher: TemplateMatchTask | None = None
        self._ui_matcher: TemplateMatchTask | None = None

        self._init_matchers()

    def _init_matchers(self) -> None:
        """Load templates from subdirectories."""
        cards_dir = self._template_dir / "cards"
        others_dir = self._template_dir / "others"
        buttons_dir = self._template_dir / "buttons"
        ui_dir = self._template_dir / "ui"

        if cards_dir.is_dir():
            self._card_matcher = TemplateMatchTask(str(cards_dir))
        if others_dir.is_dir():
            self._other_card_matcher = TemplateMatchTask(str(others_dir))
        if buttons_dir.is_dir():
            self._button_matcher = TemplateMatchTask(str(buttons_dir))
        if ui_dir.is_dir():
            self._ui_matcher = TemplateMatchTask(str(ui_dir))

        loaded = sum(1 for m in [
            self._card_matcher, self._other_card_matcher,
            self._button_matcher, self._ui_matcher
        ] if m is not None)
        logger.info("CV perception initialized: %d template sets loaded", loaded)

    async def recognize(self, image: bytes) -> PerceptionResult:
        """Run all CV tasks in parallel, return structured perception result."""
        pipeline = PerceptionPipeline(vlm_client=None)

        # Task 1: Hand cards (bottom region of screen)
        if self._card_matcher:
            pipeline.add_task(PerceptionTask(
                name="hand_cards",
                task_type="template_match",
                config={
                    "threshold": self._card_confidence,
                    "roi": (0.0, 0.75, 1.0, 1.0),  # bottom quarter
                },
            ))
            pipeline.register_custom_runner(
                "template_match",
                lambda img, task: self._run_template_match(
                    self._card_matcher, img, task.config
                ),
            )

        # Task 2: Other players' cards (center region)
        if self._other_card_matcher:
            pipeline.add_task(PerceptionTask(
                name="other_cards",
                task_type="template_match_other",
                config={
                    "threshold": self._card_confidence,
                    "roi": (0.1, 0.3, 0.9, 0.7),
                },
            ))
            pipeline.register_custom_runner(
                "template_match_other",
                lambda img, task: self._run_template_match(
                    self._other_card_matcher, img, task.config
                ),
            )

        # Task 3: Buttons (bottom-right region)
        if self._button_matcher:
            pipeline.add_task(PerceptionTask(
                name="buttons",
                task_type="template_match_buttons",
                config={
                    "threshold": self._button_confidence,
                    "roi": (0.55, 0.70, 1.0, 1.0),
                },
            ))
            pipeline.register_custom_runner(
                "template_match_buttons",
                lambda img, task: self._run_template_match(
                    self._button_matcher, img, task.config
                ),
            )

        # Task 4: UI markers (landlord icon, pass indicator, etc.)
        if self._ui_matcher:
            pipeline.add_task(PerceptionTask(
                name="ui_markers",
                task_type="template_match_ui",
                config={
                    "threshold": self._button_confidence,
                    "roi": None,  # full screen
                },
            ))
            pipeline.register_custom_runner(
                "template_match_ui",
                lambda img, task: self._run_template_match(
                    self._ui_matcher, img, task.config
                ),
            )

        result = await pipeline.run(image)
        result.parsed = self._parse_results(result)
        return result

    async def _run_template_match(
        self, matcher: TemplateMatchTask, image: bytes, config: dict
    ) -> dict[str, Any]:
        """Run a single template matcher."""
        roi = config.get("roi")
        threshold = config.get("threshold", 0.80)
        return await matcher.run(
            image, roi=roi, config={"threshold": threshold}
        )

    def _parse_results(self, result: PerceptionResult) -> dict[str, Any]:
        """Convert raw template match results to structured perception dict."""
        tasks = result.tasks_output

        # Parse hand cards
        hand_cards_raw = tasks.get("hand_cards", {})
        hand_cards = []
        card_positions = {}
        if hand_cards_raw and "matches" in hand_cards_raw:
            hand_cards, card_positions = self._parse_card_matches(
                hand_cards_raw["matches"]
            )

        # Parse other cards
        other_cards_raw = tasks.get("other_cards", {})
        last_play = []
        if other_cards_raw and "matches" in other_cards_raw:
            last_play, _ = self._parse_card_matches(other_cards_raw["matches"])

        # Parse buttons
        buttons_raw = tasks.get("buttons", {})
        buttons = []
        if buttons_raw and "matches" in buttons_raw:
            for m in buttons_raw["matches"]:
                buttons.append({
                    "text": m["template"],
                    "x": m["x"] + m["w"] // 2,
                    "y": m["y"] + m["h"] // 2,
                })

        # Parse UI markers
        ui_raw = tasks.get("ui_markers", {})
        ui_markers = {}
        if ui_raw and "matches" in ui_raw:
            for m in ui_raw["matches"]:
                ui_markers[m["template"]] = True

        # Determine phase
        button_names = [b["text"] for b in buttons]
        phase = "playing"
        if any("叫地主" in n or "抢地主" in n or "加倍" in n or "不加倍" in n for n in button_names):
            phase = "bidding"
        elif any("继续" in n for n in button_names):
            phase = "settlement"

        return {
            "phase": phase,
            "my_hand": hand_cards,
            "last_play": last_play,
            "is_pass": ui_markers.get("pass", False),
            "is_landlord": ui_markers.get("landlord", False),
            "buttons": buttons,
            "button_names": button_names,
            "card_positions": card_positions,
        }

    @staticmethod
    def _parse_card_matches(
        matches: list[dict]
    ) -> tuple[list[str], dict[str, tuple[int, int]]]:
        """Convert match results to card names and positions.
        
        Template names should follow DouZero naming:
        e.g., '3_hearts', '3_spades', 'J_clubs', 'A_diamonds', 'joker_small', 'joker_big'
        Or simplified: '3', '4', ..., 'A', '2', 'joker_small', 'joker_big'
        """
        cards = []
        positions = {}
        for m in matches:
            name = m["template"]
            cx = m["x"] + m["w"] // 2
            cy = m["y"] + m["h"] // 2
            cards.append(name)
            positions[name] = (cx, cy)
        return cards, positions
```

- [ ] **Step 2: 验证模块可导入**

```bash
cd gameauto && python -c "
from skills.doudizhu_douzero.perception import DouDiZhuDouzeroPerception
print('Perception class import OK')
# Without templates, init should still work (with warnings)
p = DouDiZhuDouzeroPerception('skills/doudizhu_douzero/assets/templates')
print('Perception init OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add gameauto/skills/doudizhu_douzero/perception.py
git commit -m "feat(doudizhu_douzero): Task 7 — CV perception layer"
```

---

### Task 8: 创建 decision.py（DouzeroDecision 决策包装）

**Files:**
- Create: `skills/doudizhu_douzero/decision.py`

- [ ] **Step 1: 写入 decision.py**

```python
"""DouzeroDecision — wraps DouZero GameEnv + DeepAgent for gameauto pipeline.

Provides a clean interface for the state handler:
  - init_round(): Initialize a new game round with perceived cards
  - decide():      Given perception dict, return Action[] for HDC execution
  - step_opponent(): Feed perceived opponent move into game state
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from gameauto.core.orchestration.base import Action
from gameauto.skills.doudizhu_douzero.douzero.env.env import Env, DummyAgent
from gameauto.skills.doudizhu_douzero.douzero.env.game import (
    EnvCard2RealCard,
    RealCard2EnvCard,
)
from gameauto.skills.doudizhu_douzero.douzero.deep_agent import DeepAgent

logger = logging.getLogger("gameauto.doudizhu_douzero")


class DouzeroDecision:
    """Decision engine using DouZero DeepAgent model.

    Wraps GameEnv for game state management and DeepAgent for
    optimal action selection.

    Usage:
        decision = DouzeroDecision(model_dir="douzero/baselines")
        decision.init_round(hand_cards, landlord_cards, my_position)
        actions = decision.decide(perception_dict)
    """

    def __init__(self, model_dir: str) -> None:
        self._model_dir = Path(model_dir)
        self._env: Env | None = None
        self._deep_agent: DeepAgent | None = None
        self._my_position: str | None = None
        self._round_initialized = False

        # Pre-load models for all positions (lazy, loaded on first use)
        self._agents: dict[str, DeepAgent] = {}

    def _get_agent(self, position: str) -> DeepAgent:
        """Lazy-load DeepAgent for given position."""
        if position not in self._agents:
            ckpt_path = self._model_dir / f"{position}.ckpt"
            if not ckpt_path.exists():
                raise FileNotFoundError(f"Model not found: {ckpt_path}")
            self._agents[position] = DeepAgent(position, str(ckpt_path))
            logger.info("Loaded DeepAgent for %s from %s", position, ckpt_path)
        return self._agents[position]

    def init_round(
        self,
        my_hand: list[str],
        landlord_cards: list[str],
        my_position: str,
        all_hands: dict[str, list[str]] | None = None,
    ) -> None:
        """Initialize a new game round.

        Args:
            my_hand: Our hand cards as display strings (e.g., ['3', 'K', 'A', ...])
            landlord_cards: 3 revealed landlord cards
            my_position: 'landlord', 'landlord_up', or 'landlord_down'
            all_hands: Optional dict with all players' hands (if fully known).
                       If None, opponents' hands are inferred from remaining deck.
        """
        self._my_position = my_position
        self._deep_agent = self._get_agent(my_position)

        # Convert card strings to env card integers
        my_hand_env = [RealCard2EnvCard[c] for c in my_hand if c in RealCard2EnvCard]
        landlord_env = [RealCard2EnvCard[c] for c in landlord_cards if c in RealCard2EnvCard]

        if all_hands is None:
            # Infer opponent hands from full deck minus ours
            from .douzero.env.env import deck
            import numpy as np
            
            _deck = deck.copy()
            np.random.shuffle(_deck)
            
            # Remove our hand and landlord cards from deck
            remaining = [c for c in _deck if c not in my_hand_env and c not in landlord_env]
            
            if my_position == 'landlord':
                all_hands = {
                    'landlord': sorted(my_hand_env),
                    'landlord_up': sorted(remaining[:17]),
                    'landlord_down': sorted(remaining[17:34]),
                }
            elif my_position == 'landlord_up':
                all_hands = {
                    'landlord_up': sorted(my_hand_env),
                    'landlord': sorted(remaining[:20]),
                    'landlord_down': sorted(remaining[20:37]),
                }
            else:  # landlord_down
                all_hands = {
                    'landlord_down': sorted(my_hand_env),
                    'landlord': sorted(remaining[:20]),
                    'landlord_up': sorted(remaining[20:37]),
                }

        # Create env with dummy agents
        agents = {
            pos: DummyAgent(pos) for pos in ['landlord', 'landlord_up', 'landlord_down']
        }
        self._env = Env(objective='wp')
        
        # Manually set up game state
        self._env._env.card_play_init({
            'landlord': all_hands['landlord'],
            'landlord_up': all_hands['landlord_up'],
            'landlord_down': all_hands['landlord_down'],
            'three_landlord_cards': landlord_env,
        })
        self._env.infoset = self._env._game_infoset
        
        self._round_initialized = True
        logger.info("Round initialized: position=%s, hand=%d cards, landlord_cards=%s",
                     my_position, len(my_hand_env), landlord_cards)

    def decide(self, perception: dict) -> list[Action]:
        """Core decision: perception → game state update → DeepAgent act → Actions.

        Args:
            perception: Structured perception dict from DouDiZhuDouzeroPerception.
                       Must contain: phase, my_hand, last_play, buttons, card_positions.

        Returns:
            List of Action objects for HDC execution.
        """
        phase = perception.get("phase", "unknown")
        buttons = perception.get("buttons", [])
        button_names = perception.get("button_names", [])

        if phase == "bidding":
            return self._decide_bidding(buttons)
        elif phase == "settlement":
            return self._decide_settlement(buttons)
        elif phase == "playing":
            return self._decide_playing(perception)
        else:
            logger.warning("Unknown phase: %s", phase)
            return []

    def _decide_bidding(self, buttons: list[dict]) -> list[Action]:
        """Bidding phase: always choose '不叫'/'不加倍' (conservative strategy)."""
        priority = ["不叫", "不加倍", "叫地主", "抢地主", "加倍"]
        for text in priority:
            for btn in buttons:
                if text in btn.get("text", ""):
                    return [Action(
                        type="tap",
                        x1=btn["x"], y1=btn["y"],
                        description=f"点击「{text}」"
                    )]
        return []

    def _decide_settlement(self, buttons: list[dict]) -> list[Action]:
        """Settlement phase: click '继续' to start next round."""
        for btn in buttons:
            if "继续" in btn.get("text", ""):
                return [Action(
                    type="tap",
                    x1=btn["x"], y1=btn["y"],
                    description="点击「继续」",
                )]
        return []

    def _decide_playing(self, perception: dict) -> list[Action]:
        """Playing phase: use DeepAgent to decide optimal play."""
        if self._env is None or self._deep_agent is None:
            logger.error("Game not initialized")
            return []

        # Check if it's our turn
        acting_pos = self._env._acting_player_position
        if acting_pos != self._my_position:
            # Not our turn — record opponent's play if any
            last_play = perception.get("last_play", [])
            is_pass = perception.get("is_pass", False)
            
            if is_pass or not last_play:
                # Opponent passed
                self._opponent_pass(acting_pos)
            else:
                self._opponent_play(acting_pos, last_play)
            
            return []  # No actions, wait for our turn

        # It's our turn — get DeepAgent's recommendation
        try:
            infoset = self._env.infoset
            action_cards, confidence = self._deep_agent.act(infoset)
        except Exception:
            logger.exception("DeepAgent.act failed")
            return self._fallback_pass()

        # Convert env card integers to display strings
        display_cards = [EnvCard2RealCard.get(c, '?') for c in action_cards]
        logger.info("DeepAgent: play=%s, win_rate=%.2f%%",
                     ''.join(display_cards), float(confidence or 0) * 100)

        if not action_cards:
            # Pass
            return self._action_pass()

        # Execute the play via env
        self._env.players[acting_pos].set_action(action_cards)
        self._env._env.step()

        # Build HDC tap actions
        return self._build_play_actions(action_cards, perception)

    def _opponent_pass(self, position: str) -> None:
        """Record opponent pass in game state."""
        self._env.players[position].set_action([])
        self._env._env.step()

    def _opponent_play(self, position: str, cards: list[str]) -> None:
        """Record opponent play in game state."""
        env_cards = [RealCard2EnvCard.get(c) for c in cards if c in RealCard2EnvCard]
        if env_cards:
            self._env.players[position].set_action(env_cards)
            self._env._env.step()

    def _build_play_actions(
        self, action_cards: list[int], perception: dict
    ) -> list[Action]:
        """Build tap actions to play cards and press '出牌' button."""
        actions = []
        card_positions = perception.get("card_positions", {})

        # Tap each card
        for card in action_cards:
            card_name = EnvCard2RealCard.get(card, str(card))
            pos = card_positions.get(card_name)
            if pos:
                actions.append(Action(
                    type="tap",
                    x1=pos[0], y1=pos[1],
                    description=f"选牌 {card_name}",
                    duration_ms=80,
                ))

        # Tap "出牌" button
        buttons = perception.get("buttons", [])
        for btn in buttons:
            if "出牌" in btn.get("text", ""):
                actions.append(Action(
                    type="tap",
                    x1=btn["x"], y1=btn["y"],
                    description="点击「出牌」",
                ))
                break

        return actions

    def _action_pass(self) -> list[Action]:
        """Build 'pass' action."""
        # The pass action sets empty list in env and returns pass tap
        if self._env:
            self._env.players[self._my_position].set_action([])
            self._env._env.step()
        return [Action(type="wait", duration_ms=500, description="要不起（跳过）")]

    def _fallback_pass(self) -> list[Action]:
        """Fallback: pass when DeepAgent fails."""
        logger.warning("DeepAgent failed, falling back to pass")
        return self._action_pass()

    @property
    def is_round_over(self) -> bool:
        """Check if current round has ended."""
        if self._env is None:
            return True
        return self._env._game_over

    @property
    def winner(self) -> str | None:
        """Get winner of current round."""
        if self._env is None:
            return None
        return self._env._game_winner if self._env._game_over else None
```

- [ ] **Step 2: 验证 decision 模块可导入**

```bash
cd gameauto && python -c "
from skills.doudizhu_douzero.decision import DouzeroDecision
print('Decision class import OK')
"
```

- [ ] **Step 3: 测试完整决策链路（模拟一局）**

```bash
cd gameauto && python -c "
from skills.doudizhu_douzero.decision import DouzeroDecision

decision = DouzeroDecision('gameauto/skills/doudizhu_douzero/douzero/baselines')

# Simulate a landlord hand (20 cards)
hand = ['3','4','5','5','6','7','8','9','T','J','Q','K','A','A','2','2','X','D','3','3']
landlord_cards = ['4','6','K']

decision.init_round(hand, landlord_cards, 'landlord')

# Simulate perception (our turn, no last play)
perception = {
    'phase': 'playing',
    'my_hand': hand,
    'last_play': [],
    'buttons': [{'text': '出牌', 'x': 800, 'y': 900}],
    'button_names': ['出牌'],
    'card_positions': {c: (100 + i*40, 800) for i, c in enumerate(hand)},
    'is_pass': False,
}

actions = decision.decide(perception)
print('Decision actions:', [a.description for a in actions])
print('Round over:', decision.is_round_over)
print('Decision chain test PASSED')
"
```

- [ ] **Step 4: Commit**

```bash
git add gameauto/skills/doudizhu_douzero/decision.py
git commit -m "feat(doudizhu_douzero): Task 8 — DouzeroDecision 决策包装"
```

---

### Task 9: 创建 visualizer.py

**Files:**
- Create: `skills/doudizhu_douzero/visualizer.py`

- [ ] **Step 1: 写入 visualizer.py**

```python
"""Lightweight visualization for DouDiZhu DouZero — card/button annotations."""

from __future__ import annotations

import io
import logging
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from gameauto.core.orchestration.base import Action

logger = logging.getLogger("gameauto.doudizhu_douzero")


def annotate_perception(image: bytes, perception: dict) -> bytes:
    """Draw bounding boxes for detected cards and buttons."""
    try:
        img = Image.open(io.BytesIO(image))
        draw = ImageDraw.Draw(img)
        
        # Draw card positions
        card_positions = perception.get("card_positions", {})
        for name, (cx, cy) in card_positions.items():
            draw.rectangle([cx - 15, cy - 20, cx + 15, cy + 20], outline="green", width=2)
            draw.text((cx - 8, cy - 18), name, fill="green")

        # Draw buttons
        for btn in perception.get("buttons", []):
            x, y = btn["x"], btn["y"]
            draw.ellipse([x - 10, y - 10, x + 10, y + 10], outline="red", width=2)
            draw.text((x + 12, y - 8), btn.get("text", ""), fill="red")

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        logger.warning("Failed to annotate perception", exc_info=True)
        return image


def annotate_actions(image: bytes, actions: list[Action]) -> bytes:
    """Draw numbered click markers for action sequence."""
    try:
        img = Image.open(io.BytesIO(image))
        draw = ImageDraw.Draw(img)

        for i, action in enumerate(actions):
            if action.type in ("tap", "swipe"):
                x, y = action.x1, action.y1
                draw.ellipse([x - 12, y - 12, x + 12, y + 12],
                             outline="blue", width=3)
                draw.text((x + 15, y - 10), str(i + 1), fill="blue")

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        logger.warning("Failed to annotate actions", exc_info=True)
        return image
```

- [ ] **Step 2: 验证可导入**

```bash
cd gameauto && python -c "
from skills.doudizhu_douzero.visualizer import annotate_perception, annotate_actions
print('Visualizer OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add gameauto/skills/doudizhu_douzero/visualizer.py
git commit -m "feat(doudizhu_douzero): Task 9 — visualizer"
```

---

### Task 10: 创建 states.py（状态注册）

**Files:**
- Create: `skills/doudizhu_douzero/states.py`

- [ ] **Step 1: 写入 states.py**

```python
"""DouDiZhu DouZero state registration — multi-state with CV detectors."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from gameauto.core.orchestration.base import Action, GameState
from gameauto.core.orchestration.context import GameContext
from gameauto.core.orchestration.state_machine import StateMachine
from gameauto.core.perception.cv.template_match import TemplateMatchTask
from gameauto.skills.doudizhu_douzero.perception import DouDiZhuDouzeroPerception
from gameauto.skills.doudizhu_douzero.decision import DouzeroDecision
from gameauto.skills.doudizhu_douzero.visualizer import annotate_perception, annotate_actions

logger = logging.getLogger("gameauto.doudizhu_douzero")

# Custom game state constants
BIDDING = "bidding"
PLAYING = "playing"
SETTLEMENT = "settlement"


class DouDiZhuDouzeroStateRegistrar:
    """Register DouZero-powered DouDiZhu states with the state machine.

    Three states with CV-based detectors:
      - BIDDING:    Template match for 叫地主/不叫/加倍 buttons
      - PLAYING:    Template match for 出牌 button
      - SETTLEMENT: Template match for 继续 button

    Usage:
        perception = DouDiZhuDouzeroPerception(template_dir=...)
        decision = DouzeroDecision(model_dir=...)
        registrar = DouDiZhuDouzeroStateRegistrar(perception, decision)
        registrar.register(state_machine)
    """

    def __init__(
        self,
        perception: DouDiZhuDouzeroPerception,
        decision: DouzeroDecision,
    ) -> None:
        self._perception = perception
        self._decision = decision

    def register(self, sm: StateMachine) -> None:
        sm.register(BIDDING, detector=self._detect_bidding, handler=self._handle)
        sm.register(PLAYING, detector=self._detect_playing, handler=self._handle)
        sm.register(SETTLEMENT, detector=self._detect_settlement, handler=self._handle)

    # ── Detectors (sync, fast template match) ────────────────────────

    def _detect_bidding(self, image: bytes) -> bool:
        """Detect bidding phase: 叫地主/不叫/抢地主/加倍/不加倍 buttons visible."""
        return self._match_any_button(image, [
            "叫地主", "不叫", "抢地主", "加倍", "不加倍"
        ])

    def _detect_playing(self, image: bytes) -> bool:
        """Detect playing phase: 出牌/不出 button visible."""
        return self._match_any_button(image, ["出牌", "不出"])

    def _detect_settlement(self, image: bytes) -> bool:
        """Detect settlement phase: 继续 button visible."""
        return self._match_any_button(image, ["继续"])

    def _match_any_button(self, image: bytes, button_names: list[str]) -> bool:
        """Check if any of the given button templates match."""
        if self._perception._button_matcher is None:
            return False
        for name in button_names:
            if self._perception._button_matcher._match_inline(
                image, roi=(0.55, 0.70, 1.0, 1.0), template_name=name,
                threshold=self._perception._button_confidence,
            ):
                return True
        return False

    # ── Handler (async, full pipeline) ───────────────────────────────

    async def _handle(self, image: bytes, context: GameContext) -> list[Action]:
        """Common handler for all states: CV perceive → decide → execute."""
        t0 = time.time()

        # 1. CV Perception
        try:
            result = await self._perception.recognize(image)
        except Exception:
            logger.exception("CV perception failed")
            return []

        state = result.parsed
        latency = (time.time() - t0) * 1000
        phase = state.get("phase", "unknown")
        n_cards = len(state.get("my_hand", []))
        n_buttons = len(state.get("buttons", []))
        logger.info("Perception: %.0fms, phase=%s, cards=%d, buttons=%d",
                     latency, phase, n_cards, n_buttons)

        # 2. Save debug output
        round_dir = self._round_dir(context)
        self._save_debug(round_dir, image, state)

        # 3. Check round initialization
        if phase == "playing" and not self._decision._round_initialized:
            # Auto-initialize on first playing frame
            self._auto_init_round(state, context)

        # 4. Decision
        actions = self._decision.decide(state)

        # 5. Check game over
        if self._decision.is_round_over:
            winner = self._decision.winner
            logger.info("Round %d over, winner: %s", context.round_num, winner)
            if context.round_num >= context.max_rounds:
                context.max_rounds = context.round_num
                logger.info("Max rounds reached — stopping")

        # 6. Save click visualization
        if actions:
            annotated = annotate_actions(image, actions)
            (round_dir / "actions.png").write_bytes(annotated)

        return actions

    def _auto_init_round(self, perception: dict, context: GameContext) -> None:
        """Auto-initialize game round from perception data."""
        my_hand = perception.get("my_hand", [])
        landlord_cards = perception.get("landlord_cards", [])
        is_landlord = perception.get("is_landlord", False)

        # Determine position
        if is_landlord:
            my_position = "landlord"
        else:
            # Heuristic: if we have 20 cards, we're landlord
            my_position = "landlord" if len(my_hand) == 20 else "landlord_up"

        try:
            self._decision.init_round(my_hand, landlord_cards, my_position)
        except Exception:
            logger.exception("Auto-init round failed")

    # ── Helpers ───────────────────────────────────────────────────

    def _round_dir(self, context: GameContext) -> Path:
        d = context.session_dir / f"round_{context.round_num:03d}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _save_debug(self, round_dir: Path, image: bytes, state: dict) -> None:
        """Save perception JSON + annotated image."""
        (round_dir / "perception.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if state:
            annotated = annotate_perception(image, state)
            (round_dir / "perception.png").write_bytes(annotated)
```

- [ ] **Step 2: 验证模块可导入**

```bash
cd gameauto && python -c "
from skills.doudizhu_douzero.states import DouDiZhuDouzeroStateRegistrar
print('StateRegistrar import OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add gameauto/skills/doudizhu_douzero/states.py
git commit -m "feat(doudizhu_douzero): Task 10 — state registration"
```

---

### Task 11: 创建 skill.py（Skill 门面）

**Files:**
- Create: `skills/doudizhu_douzero/skill.py`

- [ ] **Step 1: 写入 skill.py**

```python
"""DouDiZhuDouzeroSkill — DouZero-powered DouDiZhu Skill facade.

Usage:
    perception = DouDiZhuDouzeroPerception(template_dir=...)
    decision = DouzeroDecision(model_dir=...)
    skill = DouDiZhuDouzeroSkill(perception, decision)
    skill.register_states(state_machine)
"""

from __future__ import annotations

from gameauto.core.orchestration.state_machine import StateMachine
from gameauto.skills.doudizhu_douzero.perception import DouDiZhuDouzeroPerception
from gameauto.skills.doudizhu_douzero.decision import DouzeroDecision
from gameauto.skills.doudizhu_douzero.states import DouDiZhuDouzeroStateRegistrar


class DouDiZhuDouzeroSkill:
    """DouZero-powered DouDiZhu Skill plugin.

    Encapsulates CV perception → DeepAgent decision → HDC action pipeline.
    """

    def __init__(
        self,
        perception: DouDiZhuDouzeroPerception,
        decision: DouzeroDecision,
    ) -> None:
        self.perception = perception
        self.decision = decision
        self._registrar = DouDiZhuDouzeroStateRegistrar(perception, decision)

    def register_states(self, sm: StateMachine) -> None:
        """Register DouDiZhu DouZero states with the state machine."""
        self._registrar.register(sm)
```

- [ ] **Step 2: Commit**

```bash
git add gameauto/skills/doudizhu_douzero/skill.py
git commit -m "feat(doudizhu_douzero): Task 11 — Skill facade"
```

---

### Task 12: 创建 run_doudizhu_douzero.py（入口脚本）

**Files:**
- Create: `gameauto/run_doudizhu_douzero.py`

- [ ] **Step 1: 复制 run_match3.py 为模板**

```bash
cp gameauto/run_match3.py gameauto/run_doudizhu_douzero.py
```

- [ ] **Step 2: 修改 run_doudizhu_douzero.py**

关键修改（对照 run_match3.py 的结构）：

```python
#!/usr/bin/env python3
"""
GameAuto DouDiZhu (DouZero) — 斗地主全自动入口。

使用 DouZero 深度蒙特卡洛 AI 做决策，CV 模板匹配做感知。
无需 VLM —— 纯 CV + 深度学习方案。

快速开始:
    # 1. 配置设备 serial
    #    编辑 ~/.gameauto/settings.yaml
    # 2. 准备模板图片
    #    将手机截图的卡牌模板放入 skills/doudizhu_douzero/assets/templates/
    # 3. 运行
    python run_doudizhu_douzero.py

    # 环境变量覆盖
    ROUNDS=10 python run_doudizhu_douzero.py
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from gameauto.config.loader import load_game_config, load_global_config
from gameauto.core.orchestration.base import GameState
from gameauto.core.orchestration.context import GameContext
from gameauto.core.orchestration.state_machine import StateMachine
from gameauto.core.orchestration.loop import GameLoop
from gameauto.core.recorder.data_recorder import DataRecorder
from gameauto.core.recorder.session import SessionManager
from gameauto.skills.doudizhu_douzero.perception import DouDiZhuDouzeroPerception
from gameauto.skills.doudizhu_douzero.decision import DouzeroDecision
from gameauto.skills.doudizhu_douzero.skill import DouDiZhuDouzeroSkill
from gameauto.utils.logging import setup_logging


async def main():
    # ── Step 0: Parse CLI ────────────────────────────────────────────
    parser = argparse.ArgumentParser(description="GameAuto DouDiZhu (DouZero)")
    parser.add_argument("--config", type=str, help="Path to config file (YAML)")
    args = parser.parse_args()

    # ── Step 1: Load config ───────────────────────────────────────────
    global_cfg = load_global_config(args.config)
    game_cfg = load_game_config("doudizhu_douzero")
    rounds = int(os.environ.get("ROUNDS", game_cfg.get("max_rounds", 20)))

    # ── Step 2: Setup logging & session ───────────────────────────────
    session = SessionManager(base_dir=Path(__file__).parent / "logs")
    session_dir = session.setup({
        "game": "doudizhu_douzero",
        "rounds": rounds,
    })
    logger = setup_logging(session_dir, console_level=global_cfg.get("logging", {}).get("console_level", "INFO"))
    logger.info("GameAuto DouDiZhu (DouZero) | %d rounds", rounds)

    # ── Step 3: Connect device ────────────────────────────────────────
    device_cfg = global_cfg.get("device", {})
    serial = device_cfg.get("serial")

    from gameauto.core.capture.hdc import HdcCapture as Capture
    from gameauto.core.input.hdc import HdcInput as Input

    capture = Capture(serial)
    await capture.connect()
    input_device = Input(serial)
    await input_device.connect()

    capture_w, capture_h = capture.native_resolution
    input_device.set_input_resolution(capture_w, capture_h)
    logger.info("Device: %dx%d", capture_w, capture_h)

    # ── Step 4: Setup perception (CV, NO VLM) ──────────────────────────
    skill_dir = Path(__file__).parent / "skills" / "doudizhu_douzero"
    template_dir = skill_dir / "assets" / "templates"

    template_confidence = game_cfg.get("template_confidence", 0.90)
    card_confidence = game_cfg.get("card_confidence", 0.85)
    pass_confidence = game_cfg.get("pass_confidence", 0.90)

    perception = DouDiZhuDouzeroPerception(
        template_dir=str(template_dir),
        card_confidence=card_confidence,
        button_confidence=template_confidence,
        pass_confidence=pass_confidence,
    )
    logger.info("Perception: CV template matching | templates=%s", template_dir)

    # ── Step 5: Setup decision (DouZero DeepAgent, NO VLM) ─────────────
    model_dir = skill_dir / game_cfg.get("model_dir", "douzero/baselines")
    decision = DouzeroDecision(model_dir=str(model_dir))
    logger.info("Decision: DouZero DeepAgent | models=%s", model_dir)

    # ── Step 6: Setup skill + state machine ────────────────────────────
    skill = DouDiZhuDouzeroSkill(perception, decision)
    sm = StateMachine()
    skill.register_states(sm)

    # ── Step 7: Setup recorder ────────────────────────────────────────
    recorder = DataRecorder(session)

    # ── Step 8: Context ────────────────────────────────────────────────
    context = GameContext(
        state=GameState.PLAYING,
        session_dir=session_dir,
        capture_resolution=(capture_w, capture_h),
        input_resolution=(capture_w, capture_h),
        max_rounds=rounds,
        max_steps_per_round=9999,  # Card game — many steps per round
    )

    # ── Step 9: Run main loop ─────────────────────────────────────────
    loop = GameLoop(capture, input_device, sm, context, recorder)
    try:
        success = await loop.run()
        logger.info("Session complete: %d rounds", success)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        recorder.record_summary({
            "total_rounds": context.round_num,
            "success_count": context.success_count,
            "final_state": str(context.state),
        })
        await capture.disconnect()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
```

- [ ] **Step 2 (continued): 确认关键区别**

与 `run_match3.py` 的关键区别：
1. **无 VLM**：不 import `VlmClient`，不创建 VLM 实例
2. **无 prompt**：不加载 Jinja2 prompt 模板
3. **CV perception**：实例化 `DouDiZhuDouzeroPerception`
4. **DouZero decision**：实例化 `DouzeroDecision`
5. **max_steps_per_round = 9999**：斗地主一轮有很多步

- [ ] **Step 3: 验证导入链**

```bash
cd gameauto && python -c "
import sys
sys.path.insert(0, '..')
# Just check imports work (won't connect to device)
from gameauto.run_doudizhu_douzero import main
print('run_doudizhu_douzero.py imports OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add gameauto/run_doudizhu_douzero.py
git commit -m "feat(doudizhu_douzero): Task 12 — run script entry point"
```

---

### Task 13: 端到端烟雾测试

**Files:**
- Create: `gameauto/tools/test_doudizhu_douzero.py`

- [ ] **Step 1: 写入测试脚本**

```python
#!/usr/bin/env python3
"""
Smoke test for DouDiZhu DouZero integration.
Tests the full decision pipeline without requiring a device.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from gameauto.skills.doudizhu_douzero.decision import DouzeroDecision
from gameauto.skills.doudizhu_douzero.douzero.env.env import Env


def test_env_reset_and_step():
    """Test basic env reset and step loop."""
    print("=== Test 1: Env reset + step ===")
    env = Env(objective='wp')
    obs = env.reset()
    assert obs is not None
    assert 'legal_actions' in obs
    assert len(obs['legal_actions']) > 0
    print(f"  Reset OK: {len(obs['legal_actions'])} legal actions")
    
    # Play a few random legal moves
    for i in range(5):
        if env._game_over:
            break
        legal = obs['legal_actions']
        action = legal[i % len(legal)]
        obs, reward, done, _ = env.step(action)
        print(f"  Step {i}: action={len(action)} cards, reward={reward}, done={done}")
    
    print("  PASSED\n")


def test_deep_agent_loading():
    """Test DeepAgent model loading and inference."""
    print("=== Test 2: DeepAgent loading ===")
    model_dir = Path(__file__).parent.parent / "skills" / "doudizhu_douzero" / "douzero" / "baselines"
    
    from gameauto.skills.doudizhu_douzero.douzero.deep_agent import DeepAgent
    
    for position in ['landlord', 'landlord_up', 'landlord_down']:
        ckpt = model_dir / f"{position}.ckpt"
        if not ckpt.exists():
            print(f"  SKIP: {ckpt} not found")
            continue
        agent = DeepAgent(position, str(ckpt))
        
        # Test on a random game state
        env = Env(objective='wp')
        env.reset()
        
        # Ensure the env position matches
        from gameauto.skills.doudizhu_douzero.douzero.env.env import get_obs
        infoset = env.infoset
        action, confidence = agent.act(infoset)
        print(f"  {position}: action={len(action)} cards, confidence={confidence}")
    
    print("  PASSED\n")


def test_decision_pipeline():
    """Test the full decision pipeline with simulated perception."""
    print("=== Test 3: Decision pipeline ===")
    model_dir = str(
        Path(__file__).parent.parent / "skills" / "doudizhu_douzero" / "douzero" / "baselines"
    )
    
    decision = DouzeroDecision(model_dir)
    
    # Simulate a landlord hand
    hand = ['3','3','4','5','6','7','8','9','T','J','Q','K','A','2','X','D','5','6','7','8']
    landlord_cards = ['4','6','K']
    
    decision.init_round(hand, landlord_cards, 'landlord')
    assert decision._round_initialized
    print(f"  Init round OK: position=landlord, hand={len(hand)} cards")
    
    # Simulate first play (our turn, no opponent play yet)
    perception = {
        'phase': 'playing',
        'my_hand': hand,
        'last_play': [],
        'buttons': [{'text': '出牌', 'x': 800, 'y': 900}],
        'button_names': ['出牌'],
        'card_positions': {c: (100 + i*35, 800) for i, c in enumerate(hand)},
        'is_pass': False,
    }
    
    actions = decision.decide(perception)
    print(f"  First play: {len(actions)} actions")
    for a in actions:
        print(f"    - {a.description}")
    
    # Test bidding decision
    bid_perception = {
        'phase': 'bidding',
        'buttons': [{'text': '叫地主', 'x': 400, 'y': 700}, {'text': '不叫', 'x': 600, 'y': 700}],
        'button_names': ['叫地主', '不叫'],
    }
    bid_actions = decision.decide(bid_perception)
    print(f"  Bidding: {bid_actions[0].description if bid_actions else 'no action'}")
    assert any('不叫' in a.description for a in bid_actions)
    
    print("  PASSED\n")


def test_cv_perception_import():
    """Test perception module imports."""
    print("=== Test 4: Perception imports ===")
    from gameauto.skills.doudizhu_douzero.perception import DouDiZhuDouzeroPerception
    
    p = DouDiZhuDouzeroPerception(
        template_dir=str(Path(__file__).parent.parent / "skills" / "doudizhu_douzero" / "assets" / "templates"),
        card_confidence=0.85,
        button_confidence=0.90,
    )
    print(f"  Perception init OK (no templates loaded — expected)")
    
    from gameauto.skills.doudizhu_douzero.skill import DouDiZhuDouzeroSkill
    print(f"  Skill import OK")
    
    from gameauto.skills.doudizhu_douzero.states import DouDiZhuDouzeroStateRegistrar
    print(f"  StateRegistrar import OK")
    
    print("  PASSED\n")


if __name__ == "__main__":
    test_env_reset_and_step()
    test_deep_agent_loading()
    test_decision_pipeline()
    test_cv_perception_import()
    print("=" * 50)
    print("ALL TESTS PASSED")
    print("=" * 50)
```

- [ ] **Step 2: 运行测试**

```bash
cd gameauto && python tools/test_doudizhu_douzero.py
```

预期输出：4 个测试全部 PASS。

- [ ] **Step 3: Commit**

```bash
git add gameauto/tools/test_doudizhu_douzero.py
git commit -m "feat(doudizhu_douzero): Task 13 — smoke test script"
```

---

## 总结

共 13 个 Task，预计文件结构：

```
skills/doudizhu_douzero/
├── __init__.py
├── config.yaml
├── skill.py
├── states.py
├── perception.py
├── decision.py
├── visualizer.py
├── assets/templates/.gitkeep
└── douzero/
    ├── __init__.py
    ├── models.py
    ├── deep_agent.py
    ├── baselines/
    │   ├── landlord.ckpt
    │   ├── landlord_up.ckpt
    │   └── landlord_down.ckpt
    └── env/
        ├── __init__.py
        ├── utils.py
        ├── move_detector.py
        ├── move_generator.py
        ├── move_selector.py
        ├── game.py
        └── env.py

gameauto/
├── run_doudizhu_douzero.py
└── tools/
    └── test_doudizhu_douzero.py
```

### 依赖关系

```
Task 1 (目录+utils)  ──┐
                       ├──> Task 2 (game.py) ──┐
Task 1 (move_*.py)  ───┘                       │
                                               ├──> Task 5 (deep_agent) ──┐
                       ┌──> Task 3 (env.py)  ──┤                          │
                       │                       │                          │
Task 4 (models.py)  ───┘                       │                          │
                                               │                          │
                                               │                          │
Task 6 (config.yaml) ─────────────────────────────────────────────────────┤
Task 7 (perception.py) ───────────────────────────────────────────────────┤
                                                                           ├──> Task 10-13
Task 8 (decision.py) ─── depends on Tasks 2,3,5 ──────────────────────────┤
Task 9 (visualizer.py) ────────────────────────────────────────────────────┤
                                                                           │
Task 10 (states.py) ─── depends on Tasks 7,8,9 ────────────────────────────┤
Task 11 (skill.py)  ─── depends on Task 10 ────────────────────────────────┤
Task 12 (run_*.py)  ─── depends on Task 11 ────────────────────────────────┤
Task 13 (test)      ─── depends on Tasks 1-12 ─────────────────────────────┘
```
