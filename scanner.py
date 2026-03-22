"""
Сканер сигналов Bybit 1m
Индикаторы: RSI, EMA, Volume, Impulse candle
"""
import asyncio
import aiohttp
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

BYBIT_API = "https://api.bybit.com"


@dataclass
class Signal:
    symbol: str
    direction: str      # LONG / SHORT
    price: float
    change: float       # % за 1м
    volume: float       # объём USDT
    rsi: float
    strength: int       # 1-3 звёзды
    reasons: list[str]
    ts: str

    @property
    def uid(self) -> str:
        return f"{self.symbol}_{self.direction}_{self.ts[:4]}"  # уникальный за минуту

    def to_message(self) -> str:
        emoji = "🟢" if self.direction == "LONG" else "🔴"
        stars = "⭐" * self.strength
        reasons_text = "\n".join(f"  • {r}" for r in self.reasons)

        return (
            f"{emoji} <b>{self.direction} {self.symbol}</b> {stars}\n"
            f"💰 Цена: <b>{self.price:.4f} USDT</b>\n"
            f"📈 Движение: <b>{self.change:+.2f}%</b>\n"
            f"📦 Объём: <b>{self.volume/1000:.0f}K USDT</b>\n"
            f"📊 RSI(14): <b>{self.rsi:.1f}</b>\n"
            f"─────────────────────\n"
            f"<b>Причины сигнала:</b>\n{reasons_text}\n"
            f"─────────────────────\n"
            f"⚠️ <i>Не финансовый совет. Стоп-лосс обязателен!</i>\n"
            f"🕐 {self.ts}"
        )


class BybitScanner:
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": "Mozilla/5.0"},
                connector=aiohttp.TCPConnector(ssl=False)
            )
        return self._session

    async def fetch(self, url: str, params: dict = None) -> dict | None:
        session = await self._get_session()
        try:
            async with session.get(url, params=params,
                                   timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    return await r.json()
        except Exception as e:
            logger.warning(f"Fetch error {url}: {e}")
        return None

    async def get_top_coins(self, limit: int = 50) -> list[dict]:
        """Получить топ монеты по объёму с Bybit"""
        data = await self.fetch(
            f"{BYBIT_API}/v5/market/tickers",
            params={"category": "linear"}
        )
        if not data or data.get("retCode") != 0:
            return []

        tickers = data["result"]["list"]
        # Фильтруем только USDT пары, исключаем BTC/ETH
        altcoins = [
            t for t in tickers
            if t["symbol"].endswith("USDT")
            and not t["symbol"].startswith("BTC")
            and not t["symbol"].startswith("ETH")
            and float(t.get("turnover24h", 0)) > 0
        ]

        # Сортируем по объёму
        altcoins.sort(key=lambda x: float(x.get("turnover24h", 0)), reverse=True)

        result = []
        for t in altcoins[:limit]:
            try:
                result.append({
                    "symbol": t["symbol"],
                    "volume": float(t.get("turnover24h", 0)),
                    "change": float(t.get("price24hPcnt", 0)) * 100,
                    "price": float(t.get("lastPrice", 0)),
                })
            except Exception:
                pass

        return result

    async def get_klines(self, symbol: str, interval: str = "1", limit: int = 50) -> list[dict]:
        """Получить свечи"""
        data = await self.fetch(
            f"{BYBIT_API}/v5/market/kline",
            params={
                "category": "linear",
                "symbol": symbol,
                "interval": interval,
                "limit": limit
            }
        )
        if not data or data.get("retCode") != 0:
            return []

        klines = []
        for k in reversed(data["result"]["list"]):
            try:
                klines.append({
                    "open":   float(k[1]),
                    "high":   float(k[2]),
                    "low":    float(k[3]),
                    "close":  float(k[4]),
                    "volume": float(k[5]),
                })
            except Exception:
                pass
        return klines

    def calc_rsi(self, closes: list[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def calc_ema(self, closes: list[float], period: int) -> float:
        if len(closes) < period:
            return closes[-1] if closes else 0
        k = 2 / (period + 1)
        ema = sum(closes[:period]) / period
        for price in closes[period:]:
            ema = price * k + ema * (1 - k)
        return ema

    def analyze(self, symbol: str, klines: list[dict], min_volume: float, min_change: float) -> Optional[Signal]:
        if len(klines) < 20:
            return None

        closes = [k["close"] for k in klines]
        volumes = [k["volume"] * k["close"] for k in klines]  # объём в USDT

        last = klines[-1]
        prev = klines[-2]

        # Текущая цена и изменение за последнюю минуту
        price = last["close"]
        change_1m = (last["close"] - last["open"]) / last["open"] * 100

        # Объём последней свечи
        vol_last = last["volume"] * last["close"]
        vol_avg = sum(volumes[-20:-1]) / 19  # средний объём за 19 свечей

        # Фильтр по объёму
        if vol_last < min_volume:
            return None

        # Фильтр по движению
        if abs(change_1m) < min_change:
            return None

        rsi = self.calc_rsi(closes)
        ema9 = self.calc_ema(closes, 9)
        ema21 = self.calc_ema(closes, 21)

        reasons = []
        long_score = 0
        short_score = 0

        # 1. RSI
        if rsi < 30:
            reasons.append(f"RSI {rsi:.1f} — перепродан 🔽")
            long_score += 1
        elif rsi > 70:
            reasons.append(f"RSI {rsi:.1f} — перекуплен 🔼")
            short_score += 1

        # 2. Объём — аномальный рост
        if vol_avg > 0 and vol_last > vol_avg * 2:
            vol_ratio = vol_last / vol_avg
            reasons.append(f"Объём x{vol_ratio:.1f} от среднего 📦")
            if change_1m > 0:
                long_score += 1
            else:
                short_score += 1

        # 3. Импульсная свеча — сильный бар
        candle_body = abs(last["close"] - last["open"])
        candle_range = last["high"] - last["low"]
        if candle_range > 0 and candle_body / candle_range > 0.7:
            if change_1m > 0:
                reasons.append(f"Бычья импульсная свеча +{change_1m:.2f}% 🕯")
                long_score += 1
            else:
                reasons.append(f"Медвежья импульсная свеча {change_1m:.2f}% 🕯")
                short_score += 1

        # 4. EMA тренд
        if price > ema9 > ema21:
            reasons.append("Цена выше EMA9 > EMA21 (бычий тренд) 📈")
            long_score += 1
        elif price < ema9 < ema21:
            reasons.append("Цена ниже EMA9 < EMA21 (медвежий тренд) 📉")
            short_score += 1

        # Определяем направление
        if long_score >= 2 and long_score > short_score:
            direction = "LONG"
            strength = min(long_score, 3)
        elif short_score >= 2 and short_score > long_score:
            direction = "SHORT"
            strength = min(short_score, 3)
        else:
            return None  # Нет чёткого сигнала

        if not reasons:
            return None

        return Signal(
            symbol=symbol,
            direction=direction,
            price=price,
            change=change_1m,
            volume=vol_last,
            rsi=rsi,
            strength=strength,
            reasons=reasons,
            ts=datetime.now().strftime("%H:%M:%S")
        )

    async def scan(self, min_volume: float = 500000, min_change: float = 0.5) -> list[Signal]:
        """Сканировать топ альткоины и искать сигналы"""
        # Берём топ 30 монет по объёму
        coins = await self.get_top_coins(limit=30)
        if not coins:
            logger.warning("Не удалось получить список монет")
            return []

        logger.info(f"Сканирую {len(coins)} монет...")

        # Запрашиваем свечи параллельно (по 10 за раз)
        signals = []
        for i in range(0, len(coins), 10):
            batch = coins[i:i+10]
            tasks = [self.get_klines(c["symbol"]) for c in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for coin, klines in zip(batch, results):
                if isinstance(klines, Exception) or not klines:
                    continue
                try:
                    sig = self.analyze(coin["symbol"], klines, min_volume, min_change)
                    if sig:
                        signals.append(sig)
                        logger.info(f"Сигнал: {sig.direction} {sig.symbol} ({sig.strength}⭐)")
                except Exception as e:
                    logger.debug(f"Analyze error {coin['symbol']}: {e}")

            await asyncio.sleep(0.2)  # не спамим API

        # Сортируем по силе
        signals.sort(key=lambda s: (s.strength, abs(s.change)), reverse=True)
        logger.info(f"Найдено сигналов: {len(signals)}")
        return signals
