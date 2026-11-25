import logging
import os
from typing import Dict

import chess
import chess.engine
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
STOCKFISH_PATH = os.getenv("STOCKFISH_PATH", "stockfish")
DEPTH = int(os.getenv("ENGINE_DEPTH", "12"))

# In-memory games: restarting the service resets active games.
games: Dict[int, dict] = {}
engine = None

PIECES = {
    "P": "♙", "N": "♘", "B": "♗", "R": "♖", "Q": "♕", "K": "♔",
    "p": "♟", "n": "♞", "b": "♝", "r": "♜", "q": "♛", "k": "♚",
}

def new_game():
    return {"board": chess.Board(), "selected": None, "history": []}

def board_keyboard(game, flipped=False):
    board = game["board"]
    selected = game["selected"]
    rows = []
    ranks = range(0, 8) if flipped else range(7, -1, -1)
    files = range(7, -1, -1) if flipped else range(8)
    for rank in ranks:
        row = []
        for file_no in files:
            sq = chess.square(file_no, rank)
            piece = board.piece_at(sq)
            label = PIECES[piece.symbol()] if piece else ("·" if (file_no + rank) % 2 else " ")
            if sq == selected:
                label = f"〔{label}〕"
            elif sq in board.legal_moves and selected is not None:
                label = f"•{label}"
            row.append(InlineKeyboardButton(label, callback_data=f"sq:{chess.square_name(sq)}"))
        rows.append(row)
    rows.append([
        InlineKeyboardButton("↩️ Отменить", callback_data="undo"),
        InlineKeyboardButton("🔄 Новая партия", callback_data="reset"),
    ])
    return InlineKeyboardMarkup(rows)

def status_text(game, note=""):
    board = game["board"]
    side = "Белые" if board.turn else "Чёрные"
    text = "♟️ **Chess Helper**\n\n"
    if note:
        text += note + "\n\n"
    if board.is_checkmate():
        text += "🏁 Мат! Партия окончена."
    elif board.is_stalemate():
        text += "🤝 Пат. Ничья."
    elif board.is_game_over():
        text += "🏁 Партия окончена."
    else:
        text += f"Ход: **{side}**"
        if board.is_check():
            text += "\n⚠️ Шах!"
    return text

def engine_hint(board):
    global engine
    if engine is None:
        try:
            engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
        except Exception as exc:
            log.warning("Could not start Stockfish: %s", exc)
            return "⚠️ Stockfish пока недоступен на сервере."
    try:
        info = engine.analyse(board, chess.engine.Limit(depth=DEPTH))
        move = info.get("pv", [None])[0]
        if move is None:
            return "Подсказка недоступна."
        san = board.san(move)
        score = info.get("score")
        if score:
            cp = score.pov(board.turn).score(mate_score=10000)
            if cp is not None:
                return f"🟩 **Подсказка Stockfish:** `{move.uci()}` ({san})\nОценка: {cp / 100:+.2f}"
        return f"🟩 **Подсказка Stockfish:** `{move.uci()}` ({san})"
    except Exception as exc:
        log.exception("Engine analysis failed")
        return "⚠️ Не удалось получить подсказку Stockfish."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    games[user_id] = new_game()
    game = games[user_id]
    await update.message.reply_text(
        status_text(game, "Выбери фигуру, затем клетку назначения. После каждого хода покажу рекомендацию Stockfish."),
        reply_markup=board_keyboard(game),
        parse_mode="Markdown",
    )

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    games[update.effective_user.id] = new_game()
    game = games[update.effective_user.id]
    await update.message.reply_text(status_text(game, "Новая партия!"), reply_markup=board_keyboard(game), parse_mode="Markdown")

async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    game = games.setdefault(user_id, new_game())
    board = game["board"]
    data = query.data
    note = ""

    if data == "reset":
        games[user_id] = new_game()
        game = games[user_id]
        await query.edit_message_text(status_text(game, "Новая партия!"), reply_markup=board_keyboard(game), parse_mode="Markdown")
        return

    if data == "undo":
        if game["history"]:
            board.pop()
            game["history"].pop()
            game["selected"] = None
            note = "Последний ход отменён."
        else:
            note = "Пока нечего отменять."
        await query.edit_message_text(status_text(game, note), reply_markup=board_keyboard(game), parse_mode="Markdown")
        return

    if data.startswith("sq:"):
        sq = chess.parse_square(data.split(":", 1)[1])
        selected = game["selected"]
        if selected is None:
            piece = board.piece_at(sq)
            if piece and piece.color == board.turn:
                game["selected"] = sq
                note = f"Выбрана клетка `{chess.square_name(sq)}`. Теперь нажми клетку назначения."
            else:
                note = "Сначала выбери свою фигуру."
        else:
            move = chess.Move(selected, sq)
            piece = board.piece_at(selected)
            if piece and piece.piece_type == chess.PAWN and chess.square_rank(sq) in (0, 7):
                move.promotion = chess.QUEEN
            if move in board.legal_moves:
                san = board.san(move)
                game["history"].append(move)
                board.push(move)
                game["selected"] = None
                note = f"Твой ход: **{san}**\n\n" + engine_hint(board)
            else:
                piece = board.piece_at(sq)
                if piece and piece.color == board.turn:
                    game["selected"] = sq
                    note = f"Выбрана клетка `{chess.square_name(sq)}`."
                else:
                    game["selected"] = None
                    note = "Так сходить нельзя. Выбери фигуру ещё раз."
        await query.edit_message_text(status_text(game, note), reply_markup=board_keyboard(game), parse_mode="Markdown")

def main():
    if not TOKEN:
        raise RuntimeError("Set BOT_TOKEN in your hosting provider's environment variables.")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new", reset))
    app.add_handler(CallbackQueryHandler(handle_button))
    app.run_polling()

if __name__ == "__main__":
    main()
