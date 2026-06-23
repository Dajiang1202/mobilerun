"""中国象棋引擎 — 走法生成 + 评估 + 搜索（纯 Python，无外部依赖）。

编码约定:
  - 红方为正，黑方为负
  - 子力值: 将/帅=100000 车=1000 马=500 炮=450 象/相=200 士/仕=200 兵/卒=100
  - 棋盘: 9列(col 0-8) × 10行(row 0-9)，红方在 row=9(底线)，黑方在 row=0(底线)
  - board_pos: VLM 输出的逻辑坐标 col 1-9, row 1-10 (1=红底线, 10=黑底线)

棋子编码:
  红: King=6 Advisor=7 Elephant=8 Horse=9 Rook=10 Cannon=5 Pawn=4
  黑: 对应负值
"""

from __future__ import annotations

import copy
import logging
import random

logger = logging.getLogger("gameauto.xiangqi.engine")

# ── 常量 ──────────────────────────────────────────────────────────────

COLS = 9
ROWS = 10

# 棋子类型（绝对值）
KING = 6       # 将/帅
ADVISOR = 7    # 士/仕
ELEPHANT = 8   # 象/相
HORSE = 9      # 马
ROOK = 10      # 车
CANNON = 5     # 炮
PAWN = 4       # 兵/卒

# 子力价值
PIECE_VALUES = {
    6: 100000,   # 将/帅
    7: 200,      # 士/仕
    8: 200,      # 象/相
    9: 500,      # 马
    10: 1000,    # 车
    5: 450,      # 炮
    4: 100,      # 兵/卒
}

# 棋子中文名
PIECE_NAMES = {
    6: "帅", 7: "仕", 8: "相", 9: "馬", 10: "車", 5: "炮", 4: "兵",    # 红
    -6: "将", -7: "士", -8: "象", -9: "馬", -10: "車", -5: "炮", -4: "卒",  # 黑
}
# 红方记谱用字
RED_NAMES = {10: "车", 9: "马", 8: "相", 7: "仕", 6: "帅", 5: "炮", 4: "兵"}
BLACK_NAMES = {10: "车", 9: "马", 8: "象", 7: "士", 6: "将", 5: "炮", 4: "卒"}

# 红方列号记谱: 从右到左 一二三四五六七八九 → col 8,7,6,5,4,3,2,1,0
RED_COL_NAMES = {8: "一", 7: "二", 6: "三", 5: "四", 4: "五", 3: "六", 2: "七", 1: "八", 0: "九"}
# 黑方列号记谱: 从左到右 1-9 → col 0,1,2,3,4,5,6,7,8
BLACK_COL_NAMES = {0: "1", 1: "2", 2: "3", 3: "4", 4: "5", 5: "6", 6: "7", 7: "8", 8: "9"}

# 位置价值表（红方视角，黑方翻转row）
# 兵的位置加分
PAWN_POS_RED = [
    [0,  0,  0,  0,  0,  0,  0,  0,  0],
    [0,  0,  0,  5,  5,  5,  0,  0,  0],
    [0,  0,  5, 10, 15, 10,  5,  0,  0],
    [0,  5, 10, 20, 30, 20, 10,  5,  0],
    [5, 10, 20, 30, 40, 30, 20, 10,  5],
    [5, 10, 20, 30, 40, 30, 20, 10,  5],
    [0,  5, 10, 20, 30, 20, 10,  5,  0],
    [0,  0,  5, 10, 15, 10,  5,  0,  0],
    [0,  0,  0,  5,  5,  5,  0,  0,  0],
    [0,  0,  0,  0,  0,  0,  0,  0,  0],
]

# 马的位置加分
HORSE_POS_RED = [
    [0,  0,  0,  0,  0,  0,  0,  0,  0],
    [0,  0,  5,  5, 10,  5,  5,  0,  0],
    [0,  5, 10, 15, 15, 15, 10,  5,  0],
    [5, 10, 15, 20, 20, 20, 15, 10,  5],
    [5, 10, 15, 20, 25, 20, 15, 10,  5],
    [5, 10, 15, 20, 25, 20, 15, 10,  5],
    [0,  5, 10, 15, 15, 15, 10,  5,  0],
    [0,  0,  5,  5, 10,  5,  5,  0,  0],
    [0,  0,  0,  0,  0,  0,  0,  0,  0],
    [0,  0,  0,  0,  0,  0,  0,  0,  0],
]


# ── Board 类 ──────────────────────────────────────────────────────────

class Board:
    """中国象棋棋盘。

    内部数组: grid[row][col]  row 0-9 (0=黑底线,9=红底线)  col 0-8
    """

    def __init__(self) -> None:
        self.grid = [[0] * COLS for _ in range(ROWS)]
        self.side_to_move = 1  # 1=红方, -1=黑方

    @classmethod
    def from_pieces(cls, pieces: list[dict], side_to_move: str = "red") -> "Board":
        """从 VLM 返回的 pieces 列表构建棋盘。

        Args:
            pieces: [{"piece": "车","side": "red","board_pos":{"col":1,"row":10}}, ...]
            side_to_move: 当前轮到哪一方 ("red" 或 "black")
        """
        board = cls()
        board.side_to_move = 1 if side_to_move == "red" else -1

        for p in pieces:
            try:
                piece_name = p.get("piece", "")
                side = p.get("side", "red")
                bp = p.get("board_pos", {})
                col = int(bp.get("col", 0)) - 1   # 1-9 → 0-8
                row = int(bp.get("row", 0)) - 1   # 1-10 → 0-9
                # VLM 的 row 1=红底线 → 对应内部 row 9
                # 转换: 内部 row = 10 - VLM_row
                row = 10 - (row + 1)  # 翻转: 红底线(row=1)→内部row=9

                if 0 <= col < COLS and 0 <= row < ROWS:
                    val = _piece_id(piece_name)
                    if side == "black":
                        val = -val
                    board.grid[row][col] = val
            except (KeyError, ValueError, TypeError):
                continue

        return board

    def to_piece_list(self) -> list[dict]:
        """导出为 pieces 列表格式（用于 VLM 输出校验）。"""
        pieces = []
        for row in range(ROWS):
            for col in range(COLS):
                val = self.grid[row][col]
                if val != 0:
                    side = "red" if val > 0 else "black"
                    piece_id = abs(val)
                    name = PIECE_NAMES.get(val, "?")
                    # 内部 row → VLM row (1=红底线)
                    vlm_row = 10 - row
                    pieces.append({
                        "piece": name,
                        "side": side,
                        "board_pos": {"col": col + 1, "row": vlm_row},
                    })
        return pieces

    def to_fen(self) -> str:
        """导出为 xiangqi FEN 字符串（Pikafish/UCI 格式）。

        FEN: rows from top(black) to bottom(red), '/' separated.
        Piece letters: K=King A=Advisor B=Elephant(Bishop) N=Knight R=Rook C=Cannon P=Pawn
        Uppercase=Red, Lowercase=Black. Digits = consecutive empty squares.
        """
        rows = []
        for row in range(ROWS):  # 0=black top, 9=red bottom
            empty = 0
            fen_row = ""
            for col in range(COLS):
                val = self.grid[row][col]
                if val == 0:
                    empty += 1
                else:
                    if empty > 0:
                        fen_row += str(empty)
                        empty = 0
                    fen_row += _piece_to_fen(val)
            if empty > 0:
                fen_row += str(empty)
            rows.append(fen_row)
        fen = "/".join(rows)
        side = "w" if self.side_to_move > 0 else "b"  # red = white in UCI
        return f"{fen} {side}"

    def copy(self) -> "Board":
        b = Board()
        b.grid = copy.deepcopy(self.grid)
        b.side_to_move = self.side_to_move
        return b

    def get(self, row: int, col: int) -> int:
        if 0 <= row < ROWS and 0 <= col < COLS:
            return self.grid[row][col]
        return 0

    def set(self, row: int, col: int, val: int) -> None:
        if 0 <= row < ROWS and 0 <= col < COLS:
            self.grid[row][col] = val

    def make_move(self, move: Move) -> None:
        """执行走法（直接修改棋盘）。"""
        piece = self.grid[move.from_row][move.from_col]
        self.grid[move.from_row][move.from_col] = 0
        self.grid[move.to_row][move.to_col] = piece
        self.side_to_move = -self.side_to_move

    def unmake_move(self, move: Move, captured: int) -> None:
        """撤销走法。"""
        piece = self.grid[move.to_row][move.to_col]
        self.grid[move.to_row][move.to_col] = captured
        self.grid[move.from_row][move.from_col] = piece
        self.side_to_move = -self.side_to_move


# ── Move 数据结构 ─────────────────────────────────────────────────────

class Move:
    """一次走法。"""
    __slots__ = ("from_row", "from_col", "to_row", "to_col", "piece", "captured")

    def __init__(self, fr: int, fc: int, tr: int, tc: int, piece: int = 0, captured: int = 0):
        self.from_row = fr
        self.from_col = fc
        self.to_row = tr
        self.to_col = tc
        self.piece = piece
        self.captured = captured

    def __repr__(self) -> str:
        return f"Move({self.from_col},{self.from_row}→{self.to_col},{self.to_row})"


# ── 走法生成 ──────────────────────────────────────────────────────────

class MoveGenerator:
    """为给定方生成所有合法走法。"""

    def __init__(self, board: Board) -> None:
        self.board = board

    def generate_all(self, side: int) -> list[Move]:
        """生成 side 方 (+1=红,-1=黑) 的所有合法走法。"""
        moves = []
        for row in range(ROWS):
            for col in range(COLS):
                piece = self.board.grid[row][col]
                if piece == 0 or (piece > 0) != (side > 0):
                    continue
                gen = _GENERATORS.get(abs(piece))
                if gen:
                    gen(self.board, row, col, piece, moves)
        return moves

    def is_in_check(self, side: int) -> bool:
        """side 方是否被将军。"""
        # 找己方将的位置
        king_val = KING if side > 0 else -KING
        king_row = king_col = -1
        for row in range(ROWS):
            for col in range(COLS):
                if self.board.grid[row][col] == king_val:
                    king_row, king_col = row, col
                    break
        if king_row < 0:
            return True  # 将被吃，视为被将

        # 检查对方棋子是否攻击到将
        opp_side = -side
        for row in range(ROWS):
            for col in range(COLS):
                piece = self.board.grid[row][col]
                if piece == 0 or (piece > 0) != (opp_side > 0):
                    continue
                if _attacks_square(self.board, row, col, piece, king_row, king_col):
                    return True
        return False

    def generate_legal(self, side: int) -> list[Move]:
        """生成合法走法（排除走后被将的走法）。"""
        all_moves = self.generate_all(side)
        legal = []
        for move in all_moves:
            b = self.board
            captured = b.grid[move.to_row][move.to_col]
            b.make_move(move)
            if not self.is_in_check(side):
                legal.append(move)
            b.unmake_move(move, captured)
        return legal


# ── 各子走法生成函数 ──────────────────────────────────────────────────

def _gen_king(board: Board, row: int, col: int, piece: int, moves: list[Move]) -> None:
    """将/帅：九宫内一步直行。"""
    for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        nr, nc = row + dr, col + dc
        if _in_palace(nr, nc, piece) and _can_move(board, nr, nc, piece):
            moves.append(Move(row, col, nr, nc, piece, board.grid[nr][nc]))


def _gen_advisor(board: Board, row: int, col: int, piece: int, moves: list[Move]) -> None:
    """士/仕：九宫内一步斜行。"""
    for dr, dc in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
        nr, nc = row + dr, col + dc
        if _in_palace(nr, nc, piece) and _can_move(board, nr, nc, piece):
            moves.append(Move(row, col, nr, nc, piece, board.grid[nr][nc]))


def _gen_elephant(board: Board, row: int, col: int, piece: int, moves: list[Move]) -> None:
    """象/相：田字对角，不能过河，注意塞象眼。"""
    for dr, dc, er, ec in [(-2, -2, -1, -1), (-2, 2, -1, 1), (2, -2, 1, -1), (2, 2, 1, 1)]:
        nr, nc = row + dr, col + dc
        eye_r, eye_c = row + er, col + ec
        if (_in_own_half(nr, piece) and 0 <= nr < ROWS and 0 <= nc < COLS
                and board.grid[eye_r][eye_c] == 0
                and _can_move(board, nr, nc, piece)):
            moves.append(Move(row, col, nr, nc, piece, board.grid[nr][nc]))


def _gen_horse(board: Board, row: int, col: int, piece: int, moves: list[Move]) -> None:
    """马：日字形，注意蹩脚。"""
    leaps = [
        (-2, -1, -1, 0), (-2, 1, -1, 0), (2, -1, 1, 0), (2, 1, 1, 0),
        (-1, -2, 0, -1), (-1, 2, 0, 1), (1, -2, 0, -1), (1, 2, 0, 1),
    ]
    for dr, dc, lr, lc in leaps:
        nr, nc = row + dr, col + dc
        leg_r, leg_c = row + lr, col + lc
        if (0 <= nr < ROWS and 0 <= nc < COLS
                and board.grid[leg_r][leg_c] == 0
                and _can_move(board, nr, nc, piece)):
            moves.append(Move(row, col, nr, nc, piece, board.grid[nr][nc]))


def _gen_rook(board: Board, row: int, col: int, piece: int, moves: list[Move]) -> None:
    """车：直线行走。"""
    for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        nr, nc = row + dr, col + dc
        while 0 <= nr < ROWS and 0 <= nc < COLS:
            target = board.grid[nr][nc]
            if target == 0:
                moves.append(Move(row, col, nr, nc, piece, 0))
            else:
                if (target > 0) != (piece > 0):
                    moves.append(Move(row, col, nr, nc, piece, target))
                break
            nr += dr
            nc += dc


def _gen_cannon(board: Board, row: int, col: int, piece: int, moves: list[Move]) -> None:
    """炮：直线走无子；吃子需翻山。"""
    for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        nr, nc = row + dr, col + dc
        # 非吃子走法
        while 0 <= nr < ROWS and 0 <= nc < COLS and board.grid[nr][nc] == 0:
            moves.append(Move(row, col, nr, nc, piece, 0))
            nr += dr
            nc += dc
        # 吃子走法：找炮架
        if 0 <= nr < ROWS and 0 <= nc < COLS:
            nr += dr; nc += dc  # 跳过炮架
            while 0 <= nr < ROWS and 0 <= nc < COLS:
                target = board.grid[nr][nc]
                if target != 0:
                    if (target > 0) != (piece > 0):
                        moves.append(Move(row, col, nr, nc, piece, target))
                    break
                nr += dr
                nc += dc


def _gen_pawn(board: Board, row: int, col: int, piece: int, moves: list[Move]) -> None:
    """兵/卒：未过河只能前进，过河可左右前。"""
    forward = -1 if piece > 0 else 1  # 红向上(减row)，黑向下(加row)
    nr = row + forward
    if 0 <= nr < ROWS and _can_move(board, nr, col, piece):
        moves.append(Move(row, col, nr, col, piece, board.grid[nr][col]))
    # 过河后可左右
    if _has_crossed_river(row, piece):
        for dc in (-1, 1):
            nc = col + dc
            if 0 <= nc < COLS and _can_move(board, row, nc, piece):
                moves.append(Move(row, col, row, nc, piece, board.grid[row][nc]))


_GENERATORS = {
    KING: _gen_king, ADVISOR: _gen_advisor, ELEPHANT: _gen_elephant,
    HORSE: _gen_horse, ROOK: _gen_rook, CANNON: _gen_cannon, PAWN: _gen_pawn,
}


# ── 辅助函数 ──────────────────────────────────────────────────────────

def _piece_id(name: str) -> int:
    """棋子中文名 → 编码。覆盖简繁体及 VLM 常见变体。"""
    n = name.strip()
    m = {
        # 将/帅
        "帅": 6, "帥": 6, "将": 6, "將": 6,
        # 士/仕
        "仕": 7, "士": 7,
        # 相/象
        "相": 8, "象": 8,
        # 马
        "马": 9, "馬": 9, "码": 9,
        # 车
        "车": 10, "車": 10,
        # 炮
        "炮": 5, "砲": 5, "礮": 5,
        # 兵/卒
        "兵": 4, "卒": 4,
    }
    return m.get(n, 0)


def _can_move(board: Board, row: int, col: int, piece: int) -> bool:
    """目标格是否可走（空格或对方棋子）。"""
    target = board.grid[row][col]
    return target == 0 or (target > 0) != (piece > 0)


def _in_palace(row: int, col: int, piece: int) -> bool:
    """是否在九宫格内。"""
    if 3 <= col <= 5:
        if piece > 0:  # 红方
            return 7 <= row <= 9
        else:           # 黑方
            return 0 <= row <= 2
    return False


def _in_own_half(row: int, piece: int) -> bool:
    """是否在己方半场（象不能过河）。"""
    if piece > 0:
        return row >= 5
    return row <= 4


def _has_crossed_river(row: int, piece: int) -> bool:
    """兵/卒是否已过河。"""
    if piece > 0:
        return row <= 4
    return row >= 5


def _attacks_square(board: Board, row: int, col: int, piece: int,
                    target_row: int, target_col: int) -> bool:
    """检查 (row,col) 的棋子是否攻击 (target_row, target_col)。"""
    ptype = abs(piece)
    if ptype == KING:
        return (abs(row - target_row) + abs(col - target_col) == 1
                and _in_palace(target_row, target_col, piece))
    if ptype == ADVISOR:
        return (abs(row - target_row) == 1 and abs(col - target_col) == 1
                and _in_palace(target_row, target_col, piece))
    if ptype == ELEPHANT:
        if abs(row - target_row) == 2 and abs(col - target_col) == 2:
            eye_r = (row + target_row) // 2
            eye_c = (col + target_col) // 2
            return board.grid[eye_r][eye_c] == 0 and _in_own_half(target_row, piece)
        return False
    if ptype == HORSE:
        dr, dc = target_row - row, target_col - col
        if (abs(dr), abs(dc)) in ((2, 1), (1, 2)):
            lr = row + (1 if dr > 0 else -1 if dr < 0 else 0)
            lc = col + (1 if dc > 0 else -1 if dc < 0 else 0)
            if abs(dr) == 2:
                lr = row + (1 if dr > 0 else -1)
                lc = col
            else:
                lr = row
                lc = col + (1 if dc > 0 else -1)
            return board.grid[lr][lc] == 0
        return False
    if ptype == ROOK:
        return _line_attack(board, row, col, target_row, target_col, piece)
    if ptype == CANNON:
        return _cannon_attack(board, row, col, target_row, target_col, piece)
    if ptype == PAWN:
        forward = -1 if piece > 0 else 1
        dr = target_row - row
        dc = target_col - col
        if dr == forward and dc == 0:
            return True
        if _has_crossed_river(row, piece) and dr == 0 and abs(dc) == 1:
            return True
    return False


def _line_attack(board: Board, r: int, c: int, tr: int, tc: int, piece: int) -> bool:
    """车式直线攻击检查。"""
    if r == tr:
        step = 1 if tc > c else -1
        for cc in range(c + step, tc, step):
            if board.grid[r][cc] != 0:
                return False
        return True
    if c == tc:
        step = 1 if tr > r else -1
        for rr in range(r + step, tr, step):
            if board.grid[rr][c] != 0:
                return False
        return True
    return False


def _cannon_attack(board: Board, r: int, c: int, tr: int, tc: int, piece: int) -> bool:
    """炮式攻击检查（需翻山）。"""
    if r == tr:
        step = 1 if tc > c else -1
        count = 0
        for cc in range(c + step, tc, step):
            if board.grid[r][cc] != 0:
                count += 1
        return count == 1 and board.grid[tr][tc] != 0
    if c == tc:
        step = 1 if tr > r else -1
        count = 0
        for rr in range(r + step, tr, step):
            if board.grid[rr][c] != 0:
                count += 1
        return count == 1 and board.grid[tr][tc] != 0
    return False


# ── 评估函数 ──────────────────────────────────────────────────────────

class Evaluator:
    """局面评估。"""

    def evaluate(self, board: Board, side: int) -> int:
        """从 side 方 (+1=红,-1=黑) 视角评估。正值有利。"""
        score = 0
        for row in range(ROWS):
            for col in range(COLS):
                piece = board.grid[row][col]
                if piece == 0:
                    continue
                val = PIECE_VALUES.get(abs(piece), 0)
                # 子力价值
                if piece > 0:
                    score += val
                else:
                    score -= val
                # 位置加分
                pos_bonus = _position_bonus(row, col, piece)
                score += pos_bonus
        return score if side > 0 else -score


def _position_bonus(row: int, col: int, piece: int) -> int:
    """位置加分（红方视角）。"""
    ptype = abs(piece)
    r = row  # 红方视角行号（不变）
    c = col
    if piece < 0:
        r = 9 - row
        c = 8 - col
    if ptype == PAWN and 0 <= r < 10 and 0 <= c < 9:
        return PAWN_POS_RED[r][c]
    if ptype == HORSE and 0 <= r < 10 and 0 <= c < 9:
        return HORSE_POS_RED[r][c]
    return 0


# ── 搜索 ──────────────────────────────────────────────────────────────

class Search:
    """Minimax 搜索。"""

    def __init__(self, depth: int = 2) -> None:
        self._depth = depth
        self._evaluator = Evaluator()

    def search(self, board: Board, side: int) -> Move | None:
        """返回 side 方的最佳走法。"""
        gen = MoveGenerator(board)
        moves = gen.generate_legal(side)
        if not moves:
            return None

        best_move = None
        best_score = -99999999
        alpha = -99999999
        beta = 99999999

        for move in moves:
            captured = board.grid[move.to_row][move.to_col]
            board.make_move(move)
            score = -self._negamax(board, self._depth - 1, -beta, -alpha, -side)
            board.unmake_move(move, captured)

            if score > best_score:
                best_score = score
                best_move = move
                alpha = max(alpha, score)

        return best_move

    def _negamax(self, board: Board, depth: int, alpha: int, beta: int, side: int) -> int:
        if depth == 0:
            return self._evaluator.evaluate(board, side)

        gen = MoveGenerator(board)
        moves = gen.generate_legal(side)
        if not moves:
            return self._evaluator.evaluate(board, side) - 50000  # 无子可走惩罚

        best = -99999999
        for move in moves:
            captured = board.grid[move.to_row][move.to_col]
            board.make_move(move)
            score = -self._negamax(board, depth - 1, -beta, -alpha, -side)
            board.unmake_move(move, captured)

            best = max(best, score)
            alpha = max(alpha, score)
            if alpha >= beta:
                break

        return best


# ── 记谱 ──────────────────────────────────────────────────────────────

def to_notation(move: Move, piece: int) -> str:
    """将走法转换为中国象棋记谱（如"炮二平五"）。

    Args:
        move: 走法
        piece: 移动的棋子编码（正=红，负=黑）

    Returns:
        记谱字符串
    """
    ptype = abs(piece)
    is_red = piece > 0

    if is_red:
        name = RED_NAMES.get(ptype, "?")
        from_col_name = RED_COL_NAMES.get(move.from_col, "?")
    else:
        name = BLACK_NAMES.get(ptype, "?")
        from_col_name = BLACK_COL_NAMES.get(move.from_col, "?")

    dr = move.to_row - move.from_row

    if move.from_col == move.to_col:
        # 直走
        action = "进" if (is_red and dr < 0) or (not is_red and dr > 0) else "退"
        steps = abs(dr)
        if is_red:
            target = RED_COL_NAMES.get(move.to_col, str(steps))
        else:
            target = BLACK_COL_NAMES.get(move.to_col, str(steps))
        # 对于车炮将军，直走时目标用步数表示
        if ptype in (ROOK, CANNON, KING, PAWN):
            target = str(steps)
        return f"{name}{from_col_name}{action}{target}"

    # 平移或斜走
    action = "平"
    if is_red:
        to_name = RED_COL_NAMES.get(move.to_col, "?")
    else:
        to_name = BLACK_COL_NAMES.get(move.to_col, "?")

    # 马象士的斜走
    if ptype in (HORSE, ELEPHANT, ADVISOR):
        action = "进" if (is_red and dr < 0) or (not is_red and dr > 0) else "退"
        return f"{name}{from_col_name}{action}{to_name}"

    return f"{name}{from_col_name}{action}{to_name}"


# ── Public API ────────────────────────────────────────────────────────

_DEFAULT_SEARCH = Search(depth=2)


def find_best_move(board: Board, side: str = "red") -> dict | None:
    """从棋盘搜索最佳走法，返回包含记谱和坐标的 dict。

    Args:
        board: 棋盘
        side: 当前方 "red" 或 "black"

    Returns:
        {"notation": "炮二平五", "from": {"col":2,"row":8}, "to": {"col":5,"row":5},
         "piece": "炮", "captured": None} 或 None（无合法走法）
    """
    side_int = 1 if side == "red" else -1
    move = _DEFAULT_SEARCH.search(board, side_int)
    if move is None:
        return None

    piece = board.grid[move.from_row][move.from_col]
    piece_name = PIECE_NAMES.get(piece, "?")
    captured_val = move.captured
    captured_name = PIECE_NAMES.get(captured_val) if captured_val else None

    notation = to_notation(move, piece)

    return {
        "notation": notation,
        "from": {"col": move.from_col + 1, "row": 10 - move.from_row},
        "to": {"col": move.to_col + 1, "row": 10 - move.to_row},
        "piece": piece_name,
        "captured": captured_name,
    }


# ── FEN 辅助 ───────────────────────────────────────────────────────

_FEN_PIECE_MAP = {
    6: "K", -6: "k",       # 帅/将
    7: "A", -7: "a",       # 仕/士
    8: "B", -8: "b",       # 相/象
    9: "N", -9: "n",       # 马
    10: "R", -10: "r",     # 车
    5: "C", -5: "c",       # 炮
    4: "P", -4: "p",       # 兵/卒
}


def _piece_to_fen(val: int) -> str:
    return _FEN_PIECE_MAP.get(val, "?")


# ── Pikafish UCI 引擎集成 ──────────────────────────────────────────

import subprocess
import os
import threading
import time


class PikafishEngine:
    """Pikafish UCI 象棋引擎封装（每次搜索启动独立进程，兼容 Windows 管道）。"""

    def __init__(self, exe_path: str, threads: int = 4, hash_mb: int = 256) -> None:
        self._exe = exe_path
        self._threads = threads
        self._hash = hash_mb
        self._exe_dir = os.path.dirname(os.path.abspath(exe_path))

    def search(self, board: Board, movetime: int = 3000) -> dict | None:
        """启动 Pikafish → 发送 UCI + position + go → 逐行读到 bestmove → 退出。

        关键: 不能用 communicate(input=...)。它发送完即关闭 stdin, Pikafish 读到
        EOF 会立刻中止搜索、吐默认走法(实测秒回垃圾 a3a4, 零搜索)。必须保持 stdin
        打开, 边读 stdout 边等 bestmove, 拿到后再 quit。
        """
        fen = board.to_fen()
        try:
            proc = subprocess.Popen(
                [self._exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, cwd=self._exe_dir,
            )
        except Exception:
            return None

        cmds = (
            "uci\n"
            "isready\n"
            "setoption name Threads value %d\n"
            "setoption name Hash value %d\n"
            "position fen %s\n"
            "go movetime %d\n"
        ) % (self._threads, self._hash, fen, movetime)
        try:
            proc.stdin.write(cmds.encode())
            proc.stdin.flush()
        except Exception:
            proc.kill()
            return None

        deadline = time.monotonic() + movetime / 1000.0 + 15
        bestmove: str | None = None
        try:
            while time.monotonic() < deadline:
                line = proc.stdout.readline()
                if not line:
                    break
                line = line.decode(errors="replace").strip()
                if line.startswith("bestmove"):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1] != "(none)":
                        bestmove = parts[1]
                    break
        except Exception:
            pass
        finally:
            try:
                proc.stdin.write(b"quit\n")
                proc.stdin.flush()
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        if not bestmove:
            return None
        return self._parse_uci_move(board, bestmove)

    @staticmethod
    def _parse_uci_move(board: Board, uci: str) -> dict | None:
        """解析 UCI 走法 (如 'h2e2')。

        Pikafish UCI 列 a-i = grid col 0-8(左→右, 不翻转);
        但 rank 数字从红方底线数起(rank0=红底=grid[9]), 而 grid[0]=黑顶,
        故 grid_row = 9 - uci_rank。之前误把 uci_rank 直接当 grid 行号,
        导致走子整盘上下镜像、落子到错误棋子。
        """
        if len(uci) < 4:
            return None
        fc = ord(uci[0]) - ord('a'); fr = 9 - int(uci[1])
        tc = ord(uci[2]) - ord('a'); tr = 9 - int(uci[3])
        if not (0 <= fc < 9 and 0 <= fr <= 9 and 0 <= tc < 9 and 0 <= tr <= 9):
            return None
        piece = board.grid[fr][fc]
        if piece == 0:
            return None
        captured = board.grid[tr][tc]
        move = Move(fr, fc, tr, tc, piece, captured)
        notation = to_notation(move, piece)
        return {
            "notation": notation,
            "from": {"col": fc + 1, "row": 10 - fr},
            "to": {"col": tc + 1, "row": 10 - tr},
            "piece": PIECE_NAMES.get(piece, "?"),
            "captured": PIECE_NAMES.get(captured) if captured else None,
            "uci": uci, "engine": "pikafish",
        }


# 全局 Pikafish 实例
_pikafish: PikafishEngine | None = None
_PIKAFISH_PATH = "D:/resource/pikafish/Windows/pikafish-bmi2.exe"


def find_best_move_pikafish(board: Board, side: str = "red",
                            movetime: int = 3000) -> dict | None:
    """用 Pikafish 搜索最佳走法（ELO 3500+，公园大爷杀手）。"""
    global _pikafish
    if _pikafish is None:
        if not os.path.exists(_PIKAFISH_PATH):
            logger.warning("Pikafish not found, falling back to built-in engine")
            return find_best_move(board, side)
        _pikafish = PikafishEngine(_PIKAFISH_PATH)
    return _pikafish.search(board, movetime)
