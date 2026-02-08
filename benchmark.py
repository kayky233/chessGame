"""
Benchmark and Rule Tests for XiangqiEngine.

1. Benchmark: initial board AI move time (target < 1.0s)
2. Rule Test: Ma (Horse) blocking verification
"""

import time
from engine import XiangqiEngine


def create_initial_board():
    """Create standard Xiangqi starting position."""
    board = [[None] * 9 for _ in range(10)]

    # Black pieces (rows 0-4)
    board[0] = [
        'b_che', 'b_ma', 'b_xiang', 'b_shi', 'b_jiang',
        'b_shi', 'b_xiang', 'b_ma', 'b_che'
    ]
    board[2][1] = 'b_pao'
    board[2][7] = 'b_pao'
    for c in range(0, 9, 2):
        board[3][c] = 'b_zu'

    # Red pieces (rows 5-9)
    board[9] = [
        'r_che', 'r_ma', 'r_xiang', 'r_shi', 'r_shuai',
        'r_shi', 'r_xiang', 'r_ma', 'r_che'
    ]
    board[7][1] = 'r_pao'
    board[7][7] = 'r_pao'
    for c in range(0, 9, 2):
        board[6][c] = 'r_bing'

    return board


def benchmark():
    """Benchmark: call get_best_move 10 times on initial board."""
    print("=" * 60)
    print("  BENCHMARK: AI Move Time (Initial Board, depth=4)")
    print("=" * 60)

    engine = XiangqiEngine(depth=4)
    times = []

    for i in range(10):
        board = create_initial_board()
        t0 = time.time()
        move = engine.get_best_move(board)
        dt = time.time() - t0
        times.append(dt)

        if move:
            fr, fc, tr, tc = move
            print(f"  Run {i + 1:2d}: ({fr},{fc})->({tr},{tc})  "
                  f"time={dt:.3f}s  nodes={engine.nodes}")
        else:
            print(f"  Run {i + 1:2d}: No move  time={dt:.3f}s")

    avg = sum(times) / len(times)
    best = min(times)
    worst = max(times)
    print(f"\n  Average: {avg:.3f}s | Best: {best:.3f}s | Worst: {worst:.3f}s")
    result = avg < 1.0
    print(f"  Target: < 1.0s  {'PASS' if result else 'FAIL'}")
    return result


def test_ma_blocking():
    """Test: Ma (Horse) cannot jump when leg is blocked."""
    print("\n" + "=" * 60)
    print("  TEST: Ma (Horse) Blocking Rule")
    print("=" * 60)

    engine = XiangqiEngine()
    board = [[None] * 9 for _ in range(10)]

    # Place black Ma at center (4, 4) - should have 8 moves
    board[4][4] = 'b_ma'
    moves = engine.gen_moves(board, 'b')
    dests = {(m[2], m[3]) for m in moves if m[0] == 4 and m[1] == 4}

    expected_all = {(2, 3), (2, 5), (6, 3), (6, 5),
                    (3, 2), (3, 6), (5, 2), (5, 6)}
    assert dests == expected_all, \
        f"Without blocking: expected {expected_all}, got {dests}"
    print(f"  Ma at (4,4) without blocking: {len(dests)} moves - OK")

    # Block the leg at (3, 4) - should block moves to (2,3) and (2,5)
    board[3][4] = 'r_bing'
    moves = engine.gen_moves(board, 'b')
    dests = {(m[2], m[3]) for m in moves if m[0] == 4 and m[1] == 4}

    assert len(dests) == 6, f"With block at (3,4): expected 6 moves, got {len(dests)}"
    assert (2, 3) not in dests, "Ma should NOT reach (2,3) with block at (3,4)"
    assert (2, 5) not in dests, "Ma should NOT reach (2,5) with block at (3,4)"
    print(f"  Ma with block at (3,4): {len(dests)} moves - OK")
    print(f"  Blocked (2,3) and (2,5) correctly excluded - OK")

    # Also test: block ALL 4 legs
    board[5][4] = 'r_bing'  # blocks (6,3) and (6,5)
    board[4][3] = 'r_bing'  # blocks (3,2) and (5,2)
    board[4][5] = 'r_bing'  # blocks (3,6) and (5,6)
    moves = engine.gen_moves(board, 'b')
    dests = {(m[2], m[3]) for m in moves if m[0] == 4 and m[1] == 4}
    assert len(dests) == 0, f"All legs blocked: expected 0 moves, got {len(dests)}"
    print(f"  Ma with ALL legs blocked: {len(dests)} moves - OK")

    print("  Ma Blocking Test: PASSED")
    return True


def test_xiang_blocking():
    """Test: Xiang (Elephant) cannot move when eye is blocked or across river."""
    print("\n" + "=" * 60)
    print("  TEST: Xiang (Elephant) Eye Blocking & River Rule")
    print("=" * 60)

    engine = XiangqiEngine()
    board = [[None] * 9 for _ in range(10)]

    # Black Xiang at (2, 4) - center of black territory
    board[2][4] = 'b_xiang'
    moves = engine.gen_moves(board, 'b')
    dests = {(m[2], m[3]) for m in moves if m[0] == 2 and m[1] == 4}

    # Can go to (0,2), (0,6), (4,2), (4,6) - all within black side
    expected = {(0, 2), (0, 6), (4, 2), (4, 6)}
    assert dests == expected, f"Xiang at (2,4): expected {expected}, got {dests}"
    print(f"  Xiang at (2,4): {len(dests)} moves - OK")

    # Block eye at (3, 5) - should block move to (4, 6)
    board[3][5] = 'r_bing'
    moves = engine.gen_moves(board, 'b')
    dests = {(m[2], m[3]) for m in moves if m[0] == 2 and m[1] == 4}
    assert (4, 6) not in dests, "Xiang should NOT reach (4,6) with eye blocked"
    print(f"  Xiang with eye block at (3,5): (4,6) blocked - OK")

    # Verify Xiang cannot cross river
    board2 = [[None] * 9 for _ in range(10)]
    board2[4][4] = 'b_xiang'  # Right at the river edge
    moves = engine.gen_moves(board2, 'b')
    dests = {(m[2], m[3]) for m in moves if m[0] == 4 and m[1] == 4}
    for dr, dc in dests:
        assert dr <= 4, f"Black Xiang crossed river to row {dr}!"
    print(f"  Xiang cannot cross river - OK")

    print("  Xiang Test: PASSED")
    return True


if __name__ == '__main__':
    results = []
    results.append(("Ma Blocking", test_ma_blocking()))
    results.append(("Xiang Blocking", test_xiang_blocking()))
    results.append(("Benchmark", benchmark()))

    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    all_pass = True
    for name, ok in results:
        status = "PASS" if ok else "FAIL"
        print(f"  {name}: {status}")
        if not ok:
            all_pass = False
    print(f"\n  Overall: {'ALL PASSED' if all_pass else 'SOME FAILED'}")
    print("=" * 60)
