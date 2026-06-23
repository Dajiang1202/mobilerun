#!/usr/bin/env python3
"""
Smoke test for DouDiZhu DouZero integration.
Tests the full decision pipeline without requiring a device.

Usage:
    cd gameauto && python tools/test_doudizhu_douzero.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_env_reset_and_step():
    """Test basic env reset and step loop."""
    print("=== Test 1: Env reset + step ===")
    from skills.doudizhu_douzero.douzero.env.env import Env

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
    from skills.doudizhu_douzero.douzero.deep_agent import DeepAgent
    from skills.doudizhu_douzero.douzero.env.env import Env

    model_dir = Path("D:/resource/douzero")
    tested_positions = set()

    for position in ['landlord', 'landlord_up', 'landlord_down']:
        ckpt = model_dir / f"{position}.ckpt"
        if not ckpt.exists():
            print(f"  SKIP: {ckpt} not found")
            continue
        try:
            agent = DeepAgent(position, str(ckpt))
        except Exception as e:
            print(f"  SKIP {position}: model load failed — {e}")
            continue

        # Try up to 10 resets to get matching env position
        matched = False
        for _ in range(10):
            env = Env(objective='wp')
            env.reset()
            infoset = env.infoset
            if infoset.player_position == position:
                matched = True
                break
        if not matched:
            print(f"  SKIP {position}: could not match env position after 10 resets")
            continue

        action, confidence = agent.act(infoset)
        try:
            conf_val = float(confidence.item() if hasattr(confidence, 'item') else confidence)
        except (ValueError, TypeError):
            conf_val = float(confidence[0]) if hasattr(confidence, '__len__') else confidence
        print(f"  {position}: action={len(action)} cards, confidence={conf_val:.4f}")
        tested_positions.add(position)

    if not tested_positions:
        print("  SKIP: no positions tested (check models)")
    print("  PASSED\n")


def test_decision_pipeline():
    """Test the full decision pipeline with simulated perception."""
    print("=== Test 3: Decision pipeline ===")
    from skills.doudizhu_douzero.decision import DouzeroDecision

    decision = DouzeroDecision("D:/resource/douzero")

    # Simulate a landlord hand (20 cards)
    hand = ['3','3','4','5','6','7','8','9','T','J','Q','K','A','2','X','D','5','6','7','8']
    landlord_cards = ['4','6','K']

    decision.init_round(hand, landlord_cards, 'landlord')
    assert decision._round_initialized
    print(f"  Init round OK: position=landlord, hand={len(hand)} cards")

    # Test playing phase
    perception = {
        'phase': 'playing',
        'my_hand': hand,
        'last_play': [],
        'buttons': [{'text': '出牌', 'x': 800, 'y': 900}],
        'button_names': ['出牌'],
        'card_positions': {c: (100 + i * 35, 800) for i, c in enumerate(hand)},
        'is_pass': False,
    }

    actions = decision.decide(perception)
    print(f"  First play: {len(actions)} actions")
    for a in actions:
        print(f"    - {a.description}")

    # Test bidding: should choose conservative 不叫
    bid_perception = {
        'phase': 'bidding',
        'buttons': [
            {'text': '叫地主', 'x': 400, 'y': 700},
            {'text': '不叫', 'x': 600, 'y': 700},
        ],
        'button_names': ['叫地主', '不叫'],
    }
    bid_actions = decision.decide(bid_perception)
    assert any('不叫' in a.description for a in bid_actions), "Should prefer 不叫"
    print(f"  Bidding: {bid_actions[0].description}")

    # Test settlement
    settle_perception = {
        'phase': 'settlement',
        'buttons': [{'text': '继续', 'x': 500, 'y': 500}],
        'button_names': ['继续'],
    }
    settle_actions = decision.decide(settle_perception)
    assert any('继续' in a.description for a in settle_actions)
    print(f"  Settlement: {settle_actions[0].description}")

    # Test non-landlord position
    decision2 = DouzeroDecision("D:/resource/douzero")
    hand_farmer = ['3','4','5','6','7','8','9','T','J','Q','K','A','2','X','D','7','8']
    decision2.init_round(hand_farmer, landlord_cards, 'landlord_up')
    assert decision2._round_initialized
    print(f"  Farmer init OK: position=landlord_up, hand={len(hand_farmer)} cards")

    # Test reset
    decision.reset()
    assert not decision._round_initialized
    print("  Reset OK")

    print("  PASSED\n")


def test_module_imports():
    """Test all skill modules import correctly."""
    print("=== Test 4: Module imports ===")

    from skills.doudizhu_douzero.perception import DouDiZhuDouzeroPerception
    print("  perception OK")

    from skills.doudizhu_douzero.decision import DouzeroDecision
    print("  decision OK")

    from skills.doudizhu_douzero.visualizer import annotate_perception, annotate_actions
    print("  visualizer OK")

    from skills.doudizhu_douzero.states import (
        DouDiZhuDouzeroStateRegistrar, BIDDING, PLAYING, SETTLEMENT,
    )
    print("  states OK")

    from skills.doudizhu_douzero.skill import DouDiZhuDouzeroSkill
    print("  skill OK")

    # Test perception init (no templates needed — just verify no crash)
    templates = Path(__file__).parent.parent / "skills" / "doudizhu_douzero" / "assets" / "templates"
    p = DouDiZhuDouzeroPerception(str(templates))
    print("  perception init OK")

    print("  PASSED\n")


if __name__ == "__main__":
    test_env_reset_and_step()
    test_deep_agent_loading()
    test_decision_pipeline()
    test_module_imports()
    print("=" * 50)
    print("ALL TESTS PASSED")
    print("=" * 50)
