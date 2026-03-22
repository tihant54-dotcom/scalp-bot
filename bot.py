"""
Scalp Bot — сигналы на альткоины с Bybit
Таймфрейм: 1 минута
"""
import asyncio
import logging
import os
from datetime import datetime

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode

from scanner import BybitScanner, Signal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан!")

# ─────────────────────────────────────────────
# STATE
# ─────────────────────────────────────────────
class State:
    def __init__(self):
        self.subscribers: set[int] = set()
        self.is_auto = False
        self.min_volume = float(os.getenv("MIN_VOLUME", "500000"))   # мин. объём USDT
        self.min_change = float(os.getenv("MIN_CHANGE", "0.5"))      # мин. движение %
        self.coins: list[str] = []                                    # топ монеты

st = State()
router = Router()
scanner = BybitScanner()


# ─────────────────────────────────────────────
# KEYBOARDS
# ─────────────────────────────────────────────
def main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⚡ Скан сейчас", callback_data="scan"),
            InlineKeyboardButton(text="📊 Топ монеты", callback_data="top"),
        ],
        [
            InlineKeyboardButton(
                text=f"🔔 Авто: {'ВКЛ ✅' if st.is_auto else 'ВЫКЛ ❌'}",
                callback_data="toggle_auto"
            ),
            InlineKeyboardButton(text="⚙️ Фильтры", callback_data="filters"),
        ],
        [InlineKeyboardButton(text="ℹ️ Как читать сигнал", callback_data="help")],
    ])

def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Меню", callback_data="back")]
    ])


# ─────────────────────────────────────────────
# HANDLERS
# ─────────────────────────────────────────────
@router.message(CommandStart())
async def cmd_start(msg: Message):
    st.subscribers.add(msg.from_user.id)
    await msg.answer(
        "⚡ <b>Scalp Bot — Bybit 1m</b>\n"
        "─────────────────────\n"
        "Ищет альткоины с сильным движением на 1-минутном таймфрейме.\n\n"
        "📌 <b>Сигналы основаны на:</b>\n"
        "• RSI — перекупленность/перепроданность\n"
        "• Объём — аномальный рост\n"
        "• Свеча — сильный импульс\n"
        "• EMA — направление тренда\n\n"
        "⚠️ <i>Только для ознакомления. Торгуй на свой риск.</i>",
        reply_markup=main_kb(),
        parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data == "back")
async def cb_back(cq: CallbackQuery):
    await cq.message.edit_text(
        "⚡ <b>Scalp Bot — Bybit 1m</b>",
        reply_markup=main_kb(), parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data == "scan")
async def cb_scan(cq: CallbackQuery):
    await cq.answer("⚡ Сканирую...")
    await run_scan(cq.message)

@router.message(Command("scan"))
async def cmd_scan(msg: Message):
    await run_scan(msg)

@router.callback_query(F.data == "top")
async def cb_top(cq: CallbackQuery):
    await cq.answer()
    msg = await cq.message.answer("⏳ Загружаю топ монеты...")
    try:
        coins = await scanner.get_top_coins(limit=20)
        st.coins = coins
        text = "📊 <b>Топ-20 альткоинов по объёму (Bybit):</b>\n\n"
        for i, c in enumerate(coins, 1):
            text += f"{i}. <code>{c['symbol']}</code> — {c['volume']:,.0f} USDT | {c['change']:+.2f}%\n"
        await msg.edit_text(text, reply_markup=back_kb(), parse_mode=ParseMode.HTML)
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}", reply_markup=back_kb())

@router.callback_query(F.data == "toggle_auto")
async def cb_toggle(cq: CallbackQuery):
    st.is_auto = not st.is_auto
    if st.is_auto:
        await cq.answer("🔔 Авто-скан запущен!")
        asyncio.create_task(auto_loop(cq.bot))
    else:
        await cq.answer("🔕 Авто-скан остановлен")
    await cq.message.edit_reply_markup(reply_markup=main_kb())

@router.callback_query(F.data == "filters")
async def cb_filters(cq: CallbackQuery):
    await cq.answer()
    await cq.message.edit_text(
        f"⚙️ <b>Фильтры</b>\n\n"
        f"📦 Мин. объём: <b>{st.min_volume/1000:.0f}K USDT</b>\n"
        f"📈 Мин. движение: <b>{st.min_change}%</b>\n\n"
        f"Чтобы изменить — задай переменные в Railway:\n"
        f"<code>MIN_VOLUME</code> (сейчас: {st.min_volume:.0f})\n"
        f"<code>MIN_CHANGE</code> (сейчас: {st.min_change})",
        reply_markup=back_kb(), parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data == "help")
async def cb_help(cq: CallbackQuery):
    await cq.answer()
    await cq.message.edit_text(
        "ℹ️ <b>Как читать сигнал</b>\n\n"
        "<b>LONG 🟢</b> — сигнал на покупку\n"
        "<b>SHORT 🔴</b> — сигнал на продажу\n\n"
        "<b>Сила сигнала:</b>\n"
        "⭐⭐⭐ — очень сильный (3+ фактора)\n"
        "⭐⭐ — сильный (2 фактора)\n"
        "⭐ — слабый (1 фактор)\n\n"
        "<b>RSI</b> — индекс силы:\n"
        "• &lt;30 = перепродан → LONG\n"
        "• &gt;70 = перекуплен → SHORT\n\n"
        "<b>Объём</b> — аномальный рост объёма подтверждает движение\n\n"
        "<b>EMA</b> — цена выше EMA = бычий тренд\n\n"
        "⚠️ <i>Скальпинг = высокий риск. Используй стоп-лосс!</i>",
        reply_markup=back_kb(), parse_mode=ParseMode.HTML
    )


# ─────────────────────────────────────────────
# SCAN LOGIC
# ─────────────────────────────────────────────
async def run_scan(target: Message):
    msg = await target.answer("⏳ Сканирую Bybit 1m...")
    try:
        signals = await scanner.scan(
            min_volume=st.min_volume,
            min_change=st.min_change
        )
        await msg.delete()

        if not signals:
            await target.answer(
                f"🔍 Сигналов нет.\n"
                f"⏱ {datetime.now().strftime('%H:%M:%S')}\n"
                f"💡 Попробуй снизить MIN_CHANGE в Railway Variables.",
                reply_markup=back_kb()
            )
            return

        await target.answer(
            f"✅ <b>Найдено {len(signals)} сигналов</b> — {datetime.now().strftime('%H:%M:%S')}",
            parse_mode=ParseMode.HTML
        )
        for sig in signals[:8]:
            await target.answer(sig.to_message(), parse_mode=ParseMode.HTML)
            await asyncio.sleep(0.3)

    except Exception as e:
        await msg.delete()
        await target.answer(f"❌ Ошибка сканирования: {e}")
        logger.error(f"Scan error: {e}")


async def auto_loop(bot: Bot):
    logger.info("Авто-скан запущен")
    sent_signals: set[str] = set()

    while st.is_auto:
        try:
            signals = await scanner.scan(
                min_volume=st.min_volume,
                min_change=st.min_change
            )
            new_signals = [s for s in signals if s.uid not in sent_signals]

            if new_signals:
                hdr = (f"🔔 <b>Новые сигналы!</b> [{datetime.now().strftime('%H:%M:%S')}]\n"
                       f"Найдено: <b>{len(new_signals)}</b>")
                for uid in st.subscribers:
                    try:
                        await bot.send_message(uid, hdr, parse_mode=ParseMode.HTML)
                        for sig in new_signals[:5]:
                            await bot.send_message(uid, sig.to_message(), parse_mode=ParseMode.HTML)
                            sent_signals.add(sig.uid)
                            await asyncio.sleep(0.2)
                    except Exception as e:
                        logger.warning(f"Send {uid}: {e}")

            # Очищаем старые сигналы каждые 100 итераций
            if len(sent_signals) > 500:
                sent_signals.clear()

        except Exception as e:
            logger.error(f"Auto scan error: {e}")

        await asyncio.sleep(60)  # каждую минуту


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logger.info("Scalp Bot запущен | Bybit 1m")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
