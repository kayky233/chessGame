"""
XiangqiEngine - Chinese Chess AI with Minimax + Alpha-Beta Pruning.

STRICTLY uses Make/Unmake (Backtracking) pattern.
ZERO usage of copy.deepcopy(). Board state is modified in-place and restored.
Designed for stateless API: engine instance is created per request.
"""

import time

# ================================================================
# Piece base material values
# ================================================================
PIECE_VALUE = {
    'jiang': 10000, 'shuai': 10000,
    'che': 600,
    'pao': 285,
    'ma': 270,
    'shi': 120,
    'xiang': 120,
    'zu': 30,
    'bing': 30,
}

# ================================================================
# Piece-Square Tables (10x9, from Black's perspective)
# Black starts at rows 0-4, attacks downward.
# For Red pieces, mirror vertically: row -> 9 - row
# ================================================================

MA_PST = [
    [  0, -4,  0,  4,  4,  4,  0, -4,  0],
    [  2,  2,  4, 10, 12, 10,  4,  2,  2],
    [  4,  8, 16, 14, 18, 14, 16,  8,  4],
    [  6, 12, 16, 20, 24, 20, 16, 12,  6],
    [  8, 16, 20, 24, 28, 24, 20, 16,  8],
    [  8, 16, 20, 24, 28, 24, 20, 16,  8],
    [  6, 12, 16, 20, 24, 20, 16, 12,  6],
    [  4, 10, 16, 18, 22, 18, 16, 10,  4],
    [  2,  6, 10, 14, 16, 14, 10,  6,  2],
    [  0,  4,  6, 10, 12, 10,  6,  4,  0],
]

CHE_PST = [
    [ -2,  6,  4, 12, 12, 12,  4,  6, -2],
    [  4,  8, 10, 14, 14, 14, 10,  8,  4],
    [  4, 10, 12, 16, 18, 16, 12, 10,  4],
    [  6, 12, 14, 20, 22, 20, 14, 12,  6],
    [  8, 14, 16, 22, 24, 22, 16, 14,  8],
    [ 10, 16, 20, 24, 26, 24, 20, 16, 10],
    [ 12, 18, 22, 26, 28, 26, 22, 18, 12],
    [ 10, 16, 20, 24, 26, 24, 20, 16, 10],
    [  6, 12, 14, 18, 20, 18, 14, 12,  6],
    [  4,  8, 10, 14, 16, 14, 10,  8,  4],
]

PAO_PST = [
    [  0,  0,  2,  6,  6,  6,  2,  0,  0],
    [  0,  2,  4,  6,  6,  6,  4,  2,  0],
    [  4,  4,  4,  6, 12,  6,  4,  4,  4],
    [  0,  0,  0,  6,  6,  6,  0,  0,  0],
    [  0,  0,  0,  4,  8,  4,  0,  0,  0],
    [  0,  0,  0,  4,  8,  4,  0,  0,  0],
    [  2,  2,  2,  8, 10,  8,  2,  2,  2],
    [  2,  2,  4,  8, 12,  8,  4,  2,  2],
    [  4,  4,  6,  8, 10,  8,  6,  4,  4],
    [  0,  2,  4,  6,  8,  6,  4,  2,  0],
]

BING_PST = [
    [  0,  0,  0,  0,  0,  0,  0,  0,  0],
    [  0,  0,  0,  0,  0,  0,  0,  0,  0],
    [  0,  0,  0,  0,  0,  0,  0,  0,  0],
    [  0,  0,  0,  0,  4,  0,  0,  0,  0],
    [  2,  0,  6,  0,  8,  0,  6,  0,  2],
    [ 10, 18, 22, 30, 34, 30, 22, 18, 10],
    [ 14, 22, 30, 44, 50, 44, 30, 22, 14],
    [ 18, 26, 40, 52, 60, 52, 40, 26, 18],
    [  0,  4, 10, 16, 20, 16, 10,  4,  0],
    [  0,  0,  0,  2,  4,  2,  0,  0,  0],
]

# Map piece type to its position table
PST_MAP = {
    'ma': MA_PST,
    'che': CHE_PST,
    'pao': PAO_PST,
    'zu': BING_PST,
    'bing': BING_PST,
}

# ================================================================
# Pre-computed move patterns
# ================================================================

# Ma (Horse): (leg_dr, leg_dc, dest_dr, dest_dc)
MA_DELTAS = [
    (-1, 0, -2, -1), (-1, 0, -2,  1),
    ( 1, 0,  2, -1), ( 1, 0,  2,  1),
    ( 0, -1, -1, -2), ( 0, -1,  1, -2),
    ( 0,  1, -1,  2), ( 0,  1,  1,  2),
]

ORTHO = [(-1, 0), (1, 0), (0, -1), (0, 1)]
DIAG = [(-1, -1), (-1, 1), (1, -1), (1, 1)]


class SearchTimeout(Exception):
    """Raised when the search exceeds its wall-clock budget."""


class XiangqiEngine:
    """Chinese Chess AI Engine with Alpha-Beta Pruning and Make/Unmake."""

    def __init__(self, depth=4, time_limit_sec=None):
        self.depth = depth
        self.nodes = 0
        self.time_limit_sec = time_limit_sec if time_limit_sec and time_limit_sec > 0 else None
        self.deadline = None
        self.timed_out = False

    def _check_timeout(self):
        if self.deadline is not None and time.time() >= self.deadline:
            self.timed_out = True
            raise SearchTimeout()

    # ============================================================
    # Evaluation
    # ============================================================

    def evaluate(self, board):
        """
        Static evaluation from Black's perspective.
        Positive = good for Black, Negative = good for Red.
        Combines material values with piece-square table bonuses.
        """
        score = 0
        for r in range(10):
            row = board[r]
            for c in range(9):
                p = row[c]
                if p is None:
                    continue
                color = p[0]       # 'r' or 'b'
                ptype = p[2:]      # 'che', 'ma', 'shuai', etc.

                # Material value
                v = PIECE_VALUE.get(ptype, 0)

                # Position bonus
                tbl = PST_MAP.get(ptype)
                if tbl is not None:
                    v += tbl[r][c] if color == 'b' else tbl[9 - r][c]

                if color == 'b':
                    score += v
                else:
                    score -= v
        return score

    # ============================================================
    # Move Generation (pseudo-legal)
    # ============================================================

    def gen_moves(self, board, color):
        """Generate all pseudo-legal moves for the given color."""
        moves = []
        for r in range(10):
            row = board[r]
            for c in range(9):
                p = row[c]
                if p is None or p[0] != color:
                    continue
                pt = p[2:]
                if pt == 'che':
                    self._che(board, r, c, color, moves)
                elif pt == 'pao':
                    self._pao(board, r, c, color, moves)
                elif pt == 'ma':
                    self._ma(board, r, c, color, moves)
                elif pt == 'xiang':
                    self._xiang(board, r, c, color, moves)
                elif pt == 'shi':
                    self._shi(board, r, c, color, moves)
                elif pt in ('jiang', 'shuai'):
                    self._king(board, r, c, color, moves)
                elif pt in ('zu', 'bing'):
                    self._bing(board, r, c, color, moves)
        return moves

    def _che(self, board, r, c, color, moves):
        """Rook/Car: straight lines in 4 directions."""
        for dr, dc in ORTHO:
            nr, nc = r + dr, c + dc
            while 0 <= nr < 10 and 0 <= nc < 9:
                t = board[nr][nc]
                if t is None:
                    moves.append((r, c, nr, nc))
                else:
                    if t[0] != color:
                        moves.append((r, c, nr, nc))
                    break
                nr += dr
                nc += dc

    def _pao(self, board, r, c, color, moves):
        """Cannon: straight lines; captures by jumping over exactly one piece."""
        for dr, dc in ORTHO:
            nr, nc = r + dr, c + dc
            # Phase 1: non-capture moves (before any piece)
            while 0 <= nr < 10 and 0 <= nc < 9:
                if board[nr][nc] is None:
                    moves.append((r, c, nr, nc))
                else:
                    # Found the "screen" piece. Phase 2: look for capture.
                    nr += dr
                    nc += dc
                    while 0 <= nr < 10 and 0 <= nc < 9:
                        t = board[nr][nc]
                        if t is not None:
                            if t[0] != color:
                                moves.append((r, c, nr, nc))
                            break
                        nr += dr
                        nc += dc
                    break
                nr += dr
                nc += dc

    def _ma(self, board, r, c, color, moves):
        """Horse: L-shaped moves with mandatory leg-blocking check."""
        for ldr, ldc, ddr, ddc in MA_DELTAS:
            lr, lc = r + ldr, c + ldc
            if not (0 <= lr < 10 and 0 <= lc < 9):
                continue
            if board[lr][lc] is not None:
                continue  # Leg is blocked
            nr, nc = r + ddr, c + ddc
            if 0 <= nr < 10 and 0 <= nc < 9:
                t = board[nr][nc]
                if t is None or t[0] != color:
                    moves.append((r, c, nr, nc))

    def _xiang(self, board, r, c, color, moves):
        """Elephant: diagonal 2 steps with eye-blocking check. Cannot cross river."""
        for dr, dc in DIAG:
            nr, nc = r + 2 * dr, c + 2 * dc
            if not (0 <= nr < 10 and 0 <= nc < 9):
                continue
            # Cannot cross river
            if color == 'b' and nr > 4:
                continue
            if color == 'r' and nr < 5:
                continue
            # Check elephant eye
            if board[r + dr][c + dc] is not None:
                continue
            t = board[nr][nc]
            if t is None or t[0] != color:
                moves.append((r, c, nr, nc))

    def _shi(self, board, r, c, color, moves):
        """Advisor: diagonal 1 step within the palace."""
        for dr, dc in DIAG:
            nr, nc = r + dr, c + dc
            if not (3 <= nc <= 5):
                continue
            if color == 'b' and not (0 <= nr <= 2):
                continue
            if color == 'r' and not (7 <= nr <= 9):
                continue
            t = board[nr][nc]
            if t is None or t[0] != color:
                moves.append((r, c, nr, nc))

    def _king(self, board, r, c, color, moves):
        """King/General: orthogonal 1 step within palace + flying general."""
        for dr, dc in ORTHO:
            nr, nc = r + dr, c + dc
            if not (3 <= nc <= 5):
                continue
            if color == 'b' and not (0 <= nr <= 2):
                continue
            if color == 'r' and not (7 <= nr <= 9):
                continue
            t = board[nr][nc]
            if t is None or t[0] != color:
                moves.append((r, c, nr, nc))

        # Flying general: can capture opposing king on same column if no pieces between
        target_king = 'r_shuai' if color == 'b' else 'b_jiang'
        step = 1 if color == 'b' else -1
        nr = r + step
        while 0 <= nr < 10:
            t = board[nr][c]
            if t is not None:
                if t == target_king:
                    moves.append((r, c, nr, c))
                break
            nr += step

    def _bing(self, board, r, c, color, moves):
        """Soldier/Pawn: forward only before river; forward + sideways after river."""
        if color == 'b':
            # Black moves downward (increasing row)
            if r + 1 < 10:
                t = board[r + 1][c]
                if t is None or t[0] != color:
                    moves.append((r, c, r + 1, c))
            # After crossing river (row >= 5): sideways allowed
            if r >= 5:
                for dc in (-1, 1):
                    nc = c + dc
                    if 0 <= nc < 9:
                        t = board[r][nc]
                        if t is None or t[0] != color:
                            moves.append((r, c, r, nc))
        else:
            # Red moves upward (decreasing row)
            if r - 1 >= 0:
                t = board[r - 1][c]
                if t is None or t[0] != color:
                    moves.append((r, c, r - 1, c))
            # After crossing river (row <= 4): sideways allowed
            if r <= 4:
                for dc in (-1, 1):
                    nc = c + dc
                    if 0 <= nc < 9:
                        t = board[r][nc]
                        if t is None or t[0] != color:
                            moves.append((r, c, r, nc))

    # ============================================================
    # Move Ordering (captures first, sorted by victim value)
    # ============================================================

    def _order_moves(self, board, moves):
        """
        Order moves: captures sorted by victim value (MVV), then non-captures.
        This dramatically improves alpha-beta pruning efficiency.
        """
        captures = []
        quiet = []
        for m in moves:
            t = board[m[2]][m[3]]
            if t is not None:
                captures.append((PIECE_VALUE.get(t[2:], 0), m))
            else:
                quiet.append(m)
        captures.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in captures] + quiet

    # ============================================================
    # Negamax Search with Alpha-Beta Pruning
    # ============================================================

    def _search(self, board, depth, alpha, beta, is_black):
        """
        Negamax search with alpha-beta pruning.
        Uses Make/Unmake pattern — board is modified in-place and restored.
        Returns score from the current player's perspective.
        """
        self._check_timeout()
        self.nodes += 1

        if depth == 0:
            score = self.evaluate(board)
            return score if is_black else -score

        color = 'b' if is_black else 'r'
        moves = self.gen_moves(board, color)

        if not moves:
            # No moves = loss (checkmate / stalemate in xiangqi = loss)
            return -99999 + (self.depth - depth)

        moves = self._order_moves(board, moves)
        best = -999999

        for fr, fc, tr, tc in moves:
            self._check_timeout()

            # === MAKE MOVE ===
            captured = board[tr][tc]
            piece = board[fr][fc]
            board[tr][tc] = piece
            board[fr][fc] = None

            try:
                # If we captured the enemy king, instant win
                if captured is not None and captured[2:] in ('jiang', 'shuai'):
                    return 99999 - (self.depth - depth)

                val = -self._search(board, depth - 1, -beta, -alpha, not is_black)
            finally:
                # === UNMAKE MOVE ===
                board[fr][fc] = piece
                board[tr][tc] = captured

            if val > best:
                best = val
            if val > alpha:
                alpha = val
            if alpha >= beta:
                break  # Beta cutoff

        return best

    # ============================================================
    # Public API
    # ============================================================

    def get_best_move(self, board):
        """
        Find the best move for Black (AI).
        Returns (from_row, from_col, to_row, to_col) or None.
        Board is guaranteed to be unchanged after this call (Make/Unmake).
        """
        self.nodes = 0
        self.timed_out = False
        t0 = time.time()
        self.deadline = (
            t0 + self.time_limit_sec if self.time_limit_sec is not None else None
        )

        moves = self.gen_moves(board, 'b')
        if not moves:
            return None

        moves = self._order_moves(board, moves)

        best_move = moves[0]
        best_val = -999999
        alpha = -999999
        beta = 999999

        for fr, fc, tr, tc in moves:
            try:
                self._check_timeout()
            except SearchTimeout:
                break

            # === MAKE ===
            captured = board[tr][tc]
            piece = board[fr][fc]
            board[tr][tc] = piece
            board[fr][fc] = None

            try:
                # Instant win: captured the red king
                if captured is not None and captured[2:] in ('jiang', 'shuai'):
                    dt = time.time() - t0
                    print(f"[Engine] depth={self.depth} nodes={self.nodes} "
                          f"time={dt:.3f}s (king capture)")
                    return (fr, fc, tr, tc)

                val = -self._search(board, self.depth - 1, -beta, -alpha, False)
            except SearchTimeout:
                self.timed_out = True
                break
            finally:
                # === UNMAKE ===
                board[fr][fc] = piece
                board[tr][tc] = captured

            if val > best_val:
                best_val = val
                best_move = (fr, fc, tr, tc)
            if val > alpha:
                alpha = val

        dt = time.time() - t0
        if self.timed_out:
            print(f"[Engine] depth={self.depth} nodes={self.nodes} "
                  f"time={dt:.3f}s eval={best_val} (timeout)")
        else:
            print(f"[Engine] depth={self.depth} nodes={self.nodes} "
                  f"time={dt:.3f}s eval={best_val}")
        return best_move
