"""
=============================================================================
 V2.4 — WEB DASHBOARD (FastAPI Backend)

 Endpoints:
   GET  /api/signals         — Текущие сигналы
   GET  /api/expresses       — Текущие экспрессы
   GET  /api/bankroll        — Статистика банкролла
   GET  /api/bankroll/chart  — График банкролла за 30 дней
   GET  /api/scan            — Запустить сканирование
   GET  /api/model/metrics   — Метрики ML модели
   GET  /api/live/{match_id} — Live odds для матча
   POST /api/bet/settle      — Закрыть ставку (won/lost)
   GET  /api/health          — Health check
   WS   /ws/live             — WebSocket live updates

 Запуск:
   uvicorn dashboard.app:app --host 0.0.0.0 --port 8000 --reload
=============================================================================
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from core.fonbet_strategies import HedgeCalculator, CashoutAdvisor, SuperExpressGenerator

logger = logging.getLogger(__name__)

# Lazy imports — не падаем если не установлено
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    logger.warning("FastAPI not installed: pip install fastapi uvicorn")


# ═══════════════════════════════════════════════════════════
#  PYDANTIC MODELS (API schemas)
# ═══════════════════════════════════════════════════════════

if HAS_FASTAPI:

    class SignalResponse(BaseModel):
        id: str
        match: str
        league: str
        outcome: str
        odds: float
        bookmaker: str
        probability: float
        edge: float
        confidence: str
        stake: float
        status: str
        analysis: Optional[str] = None
        created_at: str
        commence_time: str

    class PlaceBetRequest(BaseModel):
        signal_id: str


    class ExpressResponse(BaseModel):
        id: str
        legs: int
        total_odds: float
        probability: float
        ev: float
        correlation_discount: float
        stake: float
        potential_win: float

    class BankrollResponse(BaseModel):
        bankroll: float
        initial: float
        peak: float
        drawdown: float
        total_bets: int
        settled: int
        won: int
        lost: int
        win_rate: float
        total_staked: float
        total_profit: float
        roi: float
        losing_streak: int
        kelly_fraction: float
        daily_pnl: float
        weekly_pnl: float
        is_stopped: bool
        stop_reason: str

    class ScanResponse(BaseModel):
        timestamp: str
        matches_scanned: int
        singles: int
        expresses: int
        systems: int

    class SettleBetRequest(BaseModel):
        signal_id: str
        result: str  # won, lost, void
        score: Optional[str] = None

    class StrategiesResponse(BaseModel):
        hedges: List[dict]
        cashouts: List[dict]
        toto: List[dict]

    class AnalyticsResponse(BaseModel):
        league_stats: List[dict]
        pnl_history: List[dict]
        winrate: float
        roi: float


# ─── ENDPOINTS ───

def create_app(signal_generator=None, bankroll_manager=None):
    if not HAS_FASTAPI:
        logger.error("FastAPI not installed")
        return None

    app = FastAPI(title="Betting Assistant V2 Dashboard")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    state = {
        "generator": signal_generator,
        "bankroll": bankroll_manager,
        "signals": getattr(signal_generator, "_signals_today", []) if signal_generator else [],
        "expresses": [],
        "ws_clients": set(),
        "bankroll_history": [],
    }

    # Forward reference to state for endpoints (using closure)
    

    @app.post("/api/bet/place")
    async def place_bet(req: PlaceBetRequest):
        """Ручное размещение ставки через Dashboard"""
        bm = state.get("bankroll")
        if not bm:
            raise HTTPException(503, "Bankroll manager not active")
        
        # Найти сигнал в текущем стейте
        signals = state.get("signals", [])
        signal = next((s for s in signals if s.id == req.signal_id), None)
        
        if not signal:
            # Если сигнала нет в active, возможно он уже в DB или expired
            # Но для простоты пока ищем только в active scan
            raise HTTPException(404, "Signal not found in active scan")
            
        # Формируем инфо о матче
        info = "N/A"
        if signal.match:
            outcome_str = signal.outcome.value if hasattr(signal.outcome, "value") else str(signal.outcome)
            info = f"{signal.match.home_team} vs {signal.match.away_team} ({outcome_str})"

        # Записать в БД как сделанную ставку
        bm.record_bet(signal.id, "single", signal.stake_amount, signal.bookmaker_odds, match_info=info)
        return {"status": "placed", "id": signal.id, "stake": signal.stake_amount}

    @app.get("/api/history")
    async def get_bet_history():
        from data.database import AsyncSessionLocal, SignalLog
        from sqlalchemy import select
        
        async with AsyncSessionLocal() as session:
            # Сначала берем историю из Bankroll Manager (текущая сессия)
            bm = state.get("bankroll")
            history = []
            if bm:
                for r in reversed(bm.bet_history):
                    history.append({
                        "signal_id": r.signal_id,
                        "bet_type": r.bet_type,
                        "stake": r.stake,
                        "odds": r.odds,
                        "match_info": r.match_info,
                        "result": r.result,
                        "profit": r.profit,
                        "timestamp": r.timestamp.isoformat(),
                    })
            
            # Добавляем данные из БД для тех сигналов, которые мы отслеживаем
            # (Но не дублируем, если они есть в bm.history)
            return history

    @app.get("/api/signals", response_model=List[SignalResponse])
    async def get_signals():
        from core.models import RU_BOOKMAKER_MAPPING
        return [
            SignalResponse(
                id=s.id,
                match=(f"{s.match.home_team} vs {s.match.away_team}"
                       if s.match else "N/A"),
                league=s.match.league if s.match else "",
                outcome=s.outcome.value,
                odds=s.bookmaker_odds,
                bookmaker=RU_BOOKMAKER_MAPPING.get(s.bookmaker_name, s.bookmaker_name),
                probability=s.model_probability,
                edge=s.edge,
                confidence=s.confidence_level.value,
                stake=s.stake_amount,
                status=s.status.value,
                analysis=getattr(s, "analysis", ""),
                created_at=s.created_at.isoformat(),
                commence_time=s.match.commence_time.isoformat() if s.match and s.match.commence_time else "",
            )
            for s in state.get("signals", [])
        ]
        
    # ... (other endpoints) ...
    

    @app.get("/api/expresses", response_model=List[ExpressResponse])
    async def get_expresses():
        return [
            ExpressResponse(
                id=e.id,
                legs=len(e.legs),
                total_odds=round(e.total_odds, 2),
                probability=round(e.combined_probability, 4),
                ev=round(e.adjusted_ev, 4),
                correlation_discount=e.correlation_discount,
                stake=e.stake_amount,
                potential_win=round(e.potential_win, 2),
            )
            for e in state.get("expresses", [])
        ]

    @app.get("/api/bankroll", response_model=BankrollResponse)
    async def get_bankroll():
        bm = state.get("bankroll")
        if not bm:
            raise HTTPException(404, "Bankroll manager not initialized")
        stats = bm.get_stats()
        return BankrollResponse(**stats)

    @app.get("/api/bankroll/chart")
    async def bankroll_chart():
        """График изменения банкролла (для Chart.js / Recharts)"""
        history = state.get("bankroll_history", [])
        return {
            "labels": [h["date"] for h in history],
            "data": [h["bankroll"] for h in history],
            "pnl": [h.get("pnl", 0) for h in history],
        }

    @app.get("/api/scan", response_model=ScanResponse)
    async def trigger_scan():
        gen = state.get("generator")
        if not gen:
            raise HTTPException(503, "Signal generator not ready")

        result = await gen.run_scan()
        state["signals"] = result.get("singles", [])
        state["expresses"] = result.get("expresses", [])
        state["systems"] = result.get("systems", [])
        state["last_scan"] = datetime.now(timezone.utc).replace(tzinfo=None)

        # Record bankroll history
        bm = state.get("bankroll")
        if bm:
            state["bankroll_history"].append({
                "date": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "bankroll": bm.bankroll,
                "pnl": bm._daily_pnl,
            })

        # Broadcast to WebSocket clients
        await broadcast_ws(state, {
            "type": "scan_complete",
            "signals": len(state["signals"]),
            "expresses": len(state["expresses"]),
        })

        return ScanResponse(
            matches_scanned=result.get("matches_scanned", 0),
            singles=len(state["signals"]),
            expresses=len(state["expresses"]),
            systems=len(state.get("systems", [])),
            timestamp=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        )

    @app.post("/api/bet/settle")
    async def settle_bet(req: SettleBetRequest):
        bm = state.get("bankroll")
        if not bm:
            raise HTTPException(404, "Bankroll not initialized")
        bm.settle_bet(req.signal_id, req.result)
        return {"status": "settled", "signal_id": req.signal_id,
                "result": req.result}



    @app.get("/api/analytics", response_model=AnalyticsResponse)
    async def get_analytics():
        """Сбор данных из БД для 'Базы Знаний'"""
        from data.database import AsyncSessionLocal, SignalLog
        from sqlalchemy import select, func
        
        async with AsyncSessionLocal() as session:
            # 1. Топ Лиги (по прибыли и количеству)
            # В прототипе используем данные из Bankroll, но в будущем — группировка SQL
            bm = state.get("bankroll")
            if not bm: return {"league_stats": [], "pnl_history": [], "winrate": 0, "roi": 0}
            
            stats = {}
            for b in bm.bet_history:
                if b.result == "pending": continue
                # Match info usually is "Home vs Away (Outcome)" or has league info
                # For more accuracy, we would join with SignalLog, but let's keep it simple for now
                league = b.match_info.split("(")[0].split(" vs ")[0] # Mock partition
                if league not in stats: stats[league] = {"league": league, "bets": 0, "won": 0, "profit": 0}
                stats[league]["bets"] += 1
                if b.result == "won": stats[league]["won"] += 1
                stats[league]["profit"] += b.profit
            
            league_list = sorted(stats.values(), key=lambda x: x["profit"], reverse=True)
            
            return {
                "league_stats": league_list[:15],
                "pnl_history": state.get("bankroll_history", [])[-30:],
                "winrate": bm.get_stats()["win_rate"],
                "roi": bm.get_stats()["roi"]
            }

    @app.get("/api/strategies", response_model=StrategiesResponse)
    async def get_strategies():
        """Получить рекомендации по стратегиям (Hedge, Cashout, TOTO)"""
        return {
            "hedges": [
                {
                    "express_id": "exp_123", 
                    "legs_passed": 4, 
                    "legs_total": 5,
                    "profit_guaranteed": 1250, 
                    "roi": 0.12,
                    "recommendation": "Bet 2500₽ on Draw (X) @ 3.20"
                }
            ],
            "cashouts": [],
            "toto": []
        }

    @app.get("/")
    async def get_dashboard():
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content=REACT_DASHBOARD_HTML, status_code=200)

    # ─── WEBSOCKET ───

    @app.websocket("/ws/live")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        state["ws_clients"].add(websocket)
        try:
            while True:
                data = await websocket.receive_text()
                # Клиент может подписаться на конкретные матчи
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
        except WebSocketDisconnect:
            state["ws_clients"].discard(websocket)

    return app


async def broadcast_ws(state: dict, message: dict):
    """Отправить сообщение всем WS клиентам"""
    dead = set()
    for ws in state.get("ws_clients", set()):
        try:
            await ws.send_json(message)
        except Exception:
            dead.add(ws)
    state["ws_clients"] -= dead


# ═══════════════════════════════════════════════════════════
#  REACT FRONTEND TEMPLATE (Single File)
# ═══════════════════════════════════════════════════════════

REACT_DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Betting Assistant V2 — Панель управления</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
  <script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
  <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');
    body { font-family: 'Inter', sans-serif; background: radial-gradient(circle at top right, #1a1c2c, #0d0e12); min-height: 100vh; }
    .glass { background: rgba(255, 255, 255, 0.03); backdrop-filter: blur(10px); border: 1px solid rgba(255, 255, 255, 0.05); }
    .neon-text { text-shadow: 0 0 10px rgba(96, 165, 250, 0.5); }
    .priority-badge { background: linear-gradient(135deg, #f59e0b, #d97706); }
  </style>
</head>
<body class="text-gray-100">
  <div id="root"></div>
  <script type="text/babel">
    const { useState, useEffect } = React;
    const API = window.location.origin + '/api';

    const PRIORITY_BMS = [
      "1xBet", "Melbet", "Марафон", "Pinnacle", "BetBoom", 
      "Winline", "Fonbet", "Leon", "PARI", "Лига Ставок",
      "Marathon Bet", "Зенит", "Олимп", "Бетсити", "Тенниси"
    ];

    function App() {
      const [view, setView] = useState('dashboard');
      const [bankroll, setBankroll] = useState(null);
      const [signals, setSignals] = useState([]);
      const [expresses, setExpresses] = useState([]);
      const [history, setHistory] = useState([]);
      const [strategies, setStrategies] = useState({hedges: [], cashouts: []});
      const [scanning, setScanning] = useState(false);

      const [sortConfig, setSortConfig] = useState({ key: 'edge', direction: 'desc' });

      const sortedSignals = React.useMemo(() => {
        let sortableItems = [...signals];
        if (sortConfig.key !== null) {
          sortableItems.sort((a, b) => {
            let aKey = a[sortConfig.key];
            let bKey = b[sortConfig.key];
            
            if (typeof aKey === 'string') aKey = aKey.toLowerCase();
            if (typeof bKey === 'string') bKey = bKey.toLowerCase();

            if (aKey < bKey) return sortConfig.direction === 'asc' ? -1 : 1;
            if (aKey > bKey) return sortConfig.direction === 'asc' ? 1 : -1;
            return 0;
          });
        }
        return sortableItems;
      }, [signals, sortConfig]);

      const requestSort = (key) => {
        let direction = 'asc';
        if (sortConfig.key === key && sortConfig.direction === 'asc') {
          direction = 'desc';
        }
        setSortConfig({ key, direction });
      };

      const getClassNamesFor = (key) => {
        if (sortConfig.key !== key) return 'text-gray-400 cursor-pointer hover:text-white';
        return sortConfig.direction === 'asc' ? 'text-blue-400 cursor-pointer' : 'text-orange-400 cursor-pointer';
      };

      const fetchData = async () => {
        try {
          const [bRes, sRes, eRes, hRes, stratRes, anaRes] = await Promise.all([
            fetch(API + '/bankroll').then(r => r.json()),
            fetch(API + '/signals').then(r => r.json()),
            fetch(API + '/expresses').then(r => r.json()),
            fetch(API + '/history').then(r => r.json()),
            fetch(API + '/strategies').then(r => r.json()),
            fetch(API + '/analytics').then(r => r.json()),
          ]);
          setBankroll(bRes);
          setSignals(sRes);
          setExpresses(eRes);
          setHistory(hRes || []);
          setStrategies(stratRes || {hedges: [], cashouts: []});
          setAnalytics(anaRes || {league_stats: [], pnl_history: [], winrate: 0, roi: 0});
        } catch(e) { console.error(e); }
      };

      const triggerScan = async () => {
        setScanning(true);
        try {
          await fetch(API + '/scan');
          await fetchData();
        } catch(e) { console.error(e); }
        setScanning(false);
      };

      const handlePlaceBet = async (id) => {
        try {
          const res = await fetch(API + '/bet/place', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ signal_id: id })
          });
          if (res.ok) {
            alert('✅ Ставка успешно размещена!');
            fetchData(); 
          } else {
            alert('❌ Ошибка при размещении');
          }
        } catch(e) { console.error(e); }
      };

      useEffect(() => { 
        fetchData(); 
        const i = setInterval(fetchData, 30000); 
        return () => clearInterval(i); 
      }, []);

      return (
        <div className="max-w-7xl mx-auto p-4 md:p-8">
          <header className="flex flex-col md:flex-row justify-between items-center mb-8 gap-4">
            <div>
              <h1 className="text-3xl md:text-4xl font-extrabold neon-text tracking-tight text-center md:text-left">STAVKINASPORT <span className="text-blue-500">V2</span></h1>
              <p className="text-gray-400 mt-1 text-center md:text-left">Интеллектуальный поиск Value Bets</p>
            </div>
            

            <div className="flex space-x-4">
                 {['dashboard', 'history', 'intelligence', 'strategies'].map(tab => (
                    <button key={tab} onClick={() => setView(tab)} 
                       className={`px-6 py-2 rounded-full font-bold transition-all uppercase text-sm ${view === tab ? 'bg-blue-600 text-white shadow-lg shadow-blue-900/50' : 'bg-white/5 text-gray-400 hover:text-white'}`}>
                       {tab}
                    </button>
                 ))}
            </div>

            <button onClick={triggerScan} disabled={scanning}
              className="bg-gray-800 hover:bg-gray-700 border border-white/10 text-white px-6 py-2 rounded-full font-bold disabled:opacity-50 text-sm transition-all hover:border-blue-500/50">
              {scanning ? '⏳ Сканирую...' : 'scan_market()'}
            </button>
          </header>
          
          {/* Статистика банка (Всегда видна) */}
          {bankroll && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
              {[
                { label: 'BANKROLL', val: `${bankroll.bankroll.toLocaleString()}₽`, color: 'text-blue-400' },
                { label: 'ROI', val: `${(bankroll.roi * 100).toFixed(1)}%`, color: bankroll.roi >= 0 ? 'text-green-400' : 'text-red-400' },
                { label: 'WIN RATE', val: `${(bankroll.win_rate * 100).toFixed(0)}%`, color: 'text-purple-400' },
                { label: 'TOTAL PROFIT', val: `${bankroll.total_profit > 0 ? '+' : ''}${bankroll.total_profit.toLocaleString()}₽`, color: bankroll.total_profit >= 0 ? 'text-green-400' : 'text-red-400' },
              ].map((stat, i) => (
                <div key={i} className="glass rounded-xl p-4 shadow-lg text-center md:text-left">
                  <div className="text-gray-500 text-xs font-bold uppercase tracking-widest mb-1">{stat.label}</div>
                  <div className={`text-2xl font-black ${stat.color}`}>{stat.val}</div>
                </div>
              ))}
            </div>
          )}

          {view === 'dashboard' && (
             <>
                {/* Таблица сигналов */}
                <section className="mb-12">
                    <h2 className="text-xl font-bold mb-4 text-gray-300">⚡ LIVE SIGNALS ({signals.length})</h2>
                    <div className="glass rounded-2xl overflow-hidden shadow-2xl overflow-x-auto">
                    <table className="w-full text-left min-w-[800px]">
                        <thead className="bg-white/5 uppercase text-xs font-bold text-gray-400">
                        <tr>
                            <th className={`p-4 ${getClassNamesFor('match')}`} onClick={() => requestSort('match')}>Event</th>
                            <th className="p-4 text-center">Time</th>
                            <th className={`p-4 ${getClassNamesFor('outcome')}`} onClick={() => requestSort('outcome')}>Pick</th>
                            <th className={`p-4 text-center ${getClassNamesFor('odds')}`} onClick={() => requestSort('odds')}>Odds</th>
                            <th className={`p-4 text-center ${getClassNamesFor('edge')}`} onClick={() => requestSort('edge')}>Edge</th>
                            <th className={`p-4 ${getClassNamesFor('bookmaker')}`} onClick={() => requestSort('bookmaker')}>Bookie</th>
                            <th className={`p-4 text-center ${getClassNamesFor('stake')}`} onClick={() => requestSort('stake')}>Stake</th>
                            <th className="p-4 text-center">Action</th>
                        </tr>
                        </thead>
                        <tbody className="divide-y divide-white/5">
                        {sortedSignals.map(s => (
                            <React.Fragment key={s.id}>
                            <tr className="hover:bg-white/5 transition-colors group">
                            <td className="p-4">
                                <div className="font-bold text-white group-hover:text-blue-400 transition-colors uppercase tracking-tight">{s.match}</div>
                                <div className="text-[10px] text-gray-500 font-bold uppercase">{s.league}</div>
                            </td>
                            <td className="p-4 text-center text-xs font-mono text-gray-400">
                                {s.commence_time ? new Date(s.commence_time).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'}) : '-'}
                            </td>
                            <td className="p-4">
                                <span className="px-2 py-1 rounded bg-blue-500/10 text-blue-300 font-bold text-[10px] uppercase border border-blue-500/20">
                                {s.outcome}
                                </span>
                            </td>
                            <td className="p-4 text-center font-mono text-lg text-white">{s.odds.toFixed(2)}</td>
                            <td className="p-4 text-center">
                                <div className={`font-black ${s.edge >= 0.05 ? 'text-green-400' : 'text-yellow-400'}`}>+{ (s.edge * 100).toFixed(1)}%</div>
                                <div className="text-[9px] text-gray-600 font-bold">EDGE</div>
                            </td>
                            <td className="p-4 text-xs text-gray-300 font-semibold uppercase">
                                {s.bookmaker}
                                {s.status === 'placed' && <span className="ml-2 text-xs text-green-500 font-bold">✓ AUTO</span>}
                            </td>
                            <td className="p-4 text-center">
                                <div className="text-lg font-black text-blue-400">{s.stake.toFixed(0)}₽</div>
                            </td>
                            <td className="p-4 text-center">
                                {s.status !== 'placed' ? (
                                    <button onClick={() => handlePlaceBet(s.id)} 
                                        className="bg-green-600 hover:bg-green-500 text-white font-bold py-1 px-4 rounded transition-all transform active:scale-95 shadow-lg shadow-green-900/20 text-[10px]">
                                        BET
                                    </button>
                                ) : (
                                    <span className="text-gray-500 text-[10px] font-bold">PLACED</span>
                                )}
                            </td>
                            </tr>
                            {s.analysis && (
                                <tr>
                                    <td colSpan="8" className="px-4 pb-4 pt-0">
                                        <div className="bg-white/[0.02] border border-white/5 rounded-lg p-4 text-xs text-gray-300 leading-relaxed font-mono">
                                            <div className="text-blue-400 font-bold mb-2 flex items-center gap-2">
                                                <span>🧠 AI ANALYTICS & JUSTIFICATION</span>
                                                <div className="h-[1px] flex-1 bg-blue-500/20"></div>
                                            </div>
                                            <div className="whitespace-pre-wrap">{s.analysis}</div>
                                        </div>
                                    </td>
                                </tr>
                            )}
                            </React.Fragment>
                        ))}
                        {signals.length === 0 && (
                            <tr><td colSpan="8" className="p-12 text-center text-gray-500">No signals found.</td></tr>
                        )}
                        </tbody>
                    </table>
                    </div>
                </section>
                
                {/* Экспрессы */}
                <section>
                    <h2 className="text-xl font-bold mb-4 text-gray-300">🔥 HOT EXPRESSES ({expresses.length})</h2>
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                    {expresses.map(e => (
                        <div key={e.id} className="glass rounded-2xl p-6 relative overflow-hidden group hover:bg-white/5 transition-colors">
                        <div className="flex justify-between items-start mb-4">
                            <div className="bg-orange-500/20 text-orange-400 px-2 py-1 rounded text-xs font-bold">Accumulator</div>
                            <div className="text-2xl font-black text-white">{e.total_odds.toFixed(2)}</div>
                        </div>
                        <div className="space-y-2 text-sm text-gray-400 mb-6">
                            <div className="flex justify-between"><span>Win Prob:</span> <span className="text-gray-200">{(e.probability*100).toFixed(1)}%</span></div>
                            <div className="flex justify-between"><span>Exp. Value:</span> <span className="text-green-400 font-bold">+{(e.ev*100).toFixed(1)}%</span></div>
                        </div>
                        <div className="flex items-center justify-between border-t border-white/5 pt-4">
                             <div className="text-3xl font-bold text-blue-400">{e.stake.toFixed(0)}₽</div>
                             <div className="text-right">
                                <div className="text-xs text-gray-500">POTENTIAL WIN</div>
                                <div className="text-green-400 font-bold">{e.potential_win.toFixed(0)}₽</div>
                             </div>
                        </div>
                        </div>
                    ))}
                    </div>
                </section>
             </>
          )}

           {view === 'history' && (
               <section>
                 <div className="flex justify-between items-center mb-6">
                    <h2 className="text-xl font-bold text-gray-300">📜 BET HISTORY ({history.length})</h2>
                    <button onClick={fetchData} className="text-blue-400 text-sm hover:text-white">Refresh</button>
                 </div>
                 <div className="glass rounded-2xl overflow-hidden shadow-2xl overflow-x-auto">
                   <table className="w-full text-left min-w-[800px]">
                     <thead className="bg-white/5 uppercase text-xs font-bold text-gray-400">
                       <tr>
                         <th className="p-4 text-center">Action</th>
                         <th className="p-4">Date</th>
                         <th className="p-4">Match / Selection</th>
                         <th className="p-4">Type</th>
                         <th className="p-4 text-center">Odds</th>
                         <th className="p-4 text-center">Stake</th>
                         <th className="p-4 text-center">Result</th>
                         <th className="p-4 text-right">Profit</th>
                       </tr>
                     </thead>
                     <tbody className="divide-y divide-white/5 text-sm">
                       {history.map((h, i) => (
                         <tr key={i} className="hover:bg-white/5 transition-colors">
                            <td className="p-4 text-center">
                               {h.result === 'pending' && (
                                 <div className="flex gap-1 justify-center">
                                    <button onClick={() => {
                                        const res = prompt('Result (won/lost/void):', 'won');
                                        if (res) fetch(API + '/bet/settle', {
                                            method: 'POST', 
                                            headers: {'Content-Type': 'application/json'},
                                            body: JSON.stringify({ signal_id: h.signal_id, result: res })
                                        }).then(() => fetchData());
                                    }} className="text-green-500 hover:bg-green-500/10 p-1 rounded">✔</button>
                                 </div>
                               )}
                            </td>
                            <td className="p-4 font-mono text-gray-500 whitespace-nowrap">{new Date(h.timestamp).toLocaleString()}</td>
                            <td className="p-4">
                                 <div className="font-bold text-gray-200">{h.match_info || h.signal_id}</div>
                            </td>
                            <td className="p-4 text-xs uppercase text-gray-500">{h.bet_type}</td>
                            <td className="p-4 text-center font-mono text-white">{h.odds.toFixed(2)}</td>
                            <td className="p-4 text-center text-blue-400 font-bold">{h.stake.toFixed(0)}₽</td>
                            <td className="p-4 text-center">
                              <span className={`px-2 py-1 rounded text-xs font-bold uppercase border ${
                                h.result === 'won' ? 'bg-green-500/20 text-green-400 border-green-500/30' :
                                h.result === 'lost' ? 'bg-red-500/20 text-red-400 border-red-500/30' :
                                'bg-yellow-500/10 text-yellow-400 border-yellow-500/20'
                              }`}>
                                {h.result}
                              </span>
                            </td>
                            <td className={`p-4 text-right font-mono font-bold ${h.profit > 0 ? 'text-green-400' : h.profit < 0 ? 'text-red-400' : 'text-gray-500'}`}>
                              {h.profit > 0 ? '+' : ''}{h.profit.toFixed(0)}₽
                            </td>
                         </tr>
                       ))}
                       {history.length === 0 && (
                             <tr><td colSpan="8" className="p-12 text-center text-gray-500">История пуста.</td></tr>
                       )}
                     </tbody>
                   </table>
                 </div>
               </section>
           )}

           {view === 'intelligence' && (
              <section className="space-y-8">
                 <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                    <div className="glass rounded-2xl p-6">
                        <h3 className="text-lg font-bold mb-4 text-blue-400 uppercase tracking-tighter">🏆 Лиги-лидеры (База знаний)</h3>
                        <div className="space-y-2">
                           {analytics.league_stats.length > 0 ? analytics.league_stats.map((l, i) => (
                              <div key={i} className="flex justify-between items-center bg-white/5 p-3 rounded-lg border border-white/5">
                                 <div>
                                    <div className="font-bold text-white">{l.league}</div>
                                    <div className="text-xs text-gray-400">Сделок: {l.bets} | WinRate: {((l.won/l.bets)*100).toFixed(0)}%</div>
                                 </div>
                                 <div className={`font-mono font-bold ${l.profit > 0 ? 'text-green-400' : 'text-red-400'}`}>
                                    {l.profit > 0 ? '+' : ''}{l.profit.toFixed(0)}₽
                                 </div>
                              </div>
                           )) : (
                              <div className="text-gray-500 italic py-4">Недостаточно данных для анализа лиг...</div>
                           )}
                        </div>
                    </div>
                    <div className="glass rounded-2xl p-6 flex flex-col justify-center items-center text-center">
                        <div className="text-xs text-gray-500 mb-2 font-bold uppercase tracking-widest">Аналитическая модель (ROI)</div>
                        <div className={`text-6xl font-black mb-2 ${analytics.roi >= 0 ? 'text-blue-500' : 'text-red-500'}`}>
                           {(analytics.roi * 100).toFixed(1)}%
                        </div>
                        <p className="text-gray-400 text-sm max-w-[250px]">
                           Бот учится на исходах: {analytics.winrate.toFixed(1)}% точности в последних сделках.
                        </p>
                    </div>
                 </div>

                 <div className="glass rounded-2xl p-8">
                    <div className="flex justify-between items-center mb-4">
                        <h3 className="text-xl font-bold text-gray-200">📊 Динамика доходности</h3>
                        <div className="text-xs font-bold text-blue-400">ПОСЛЕДНИЕ 30 ДНЕЙ</div>
                    </div>
                    <div className="h-48 flex items-end gap-1 px-2 border-b border-white/5 pb-2">
                        {analytics.pnl_history.length > 0 ? analytics.pnl_history.map((h, i) => (
                           <div key={i} className={`flex-1 rounded-t-sm transition-all hover:opacity-80 ${h.pnl >= 0 ? 'bg-blue-500/30 border-blue-500/50' : 'bg-red-500/30 border-red-500/50'}`} 
                                style={{height: `${Math.min(100, Math.max(10, Math.abs(h.pnl) / 100))}%`}}
                                title={`${new Date(h.date).toLocaleDateString()}: ${h.pnl}₽`}></div>
                        )) : (
                           <div className="w-full text-center text-gray-600 font-mono">Ожидание накопления статистики...</div>
                        )}
                    </div>
                 </div>
              </section>
           )}

           {view === 'strategies' && (
              <section className="space-y-8">
                  <div className="glass rounded-2xl p-8 border-l-4 border-purple-500">
                    <h2 className="text-2xl font-bold mb-4 flex items-center gap-3">
                        <span className="text-3xl">🛡️</span> Hedge Strategies (Противоход)
                    </h2>
                    <p className="text-gray-400 mb-6">Бот автоматически предлагает хеджирование для экспрессов, где прошла большая часть событий.</p>
                    
                    {/* Hedges List */}
                    {strategies && strategies.hedges && strategies.hedges.length > 0 ? (
                        strategies.hedges.map((h, i) => (
                            <div key={i} className="bg-white/5 rounded-xl p-6 border border-white/10 mb-4">
                                <div className="flex justify-between items-start mb-4">
                                    <div>
                                        <div className="text-sm text-gray-500 uppercase font-bold">Express #{h.express_id}</div>
                                        <div className="text-xl font-bold text-white">{h.legs_passed} / {h.legs_total} Legs Passed</div>
                                    </div>
                                    <div className="text-green-400 font-bold bg-green-500/10 px-3 py-1 rounded">+{h.profit_guaranteed}₽ Guaranteed</div>
                                </div>
                                <div className="flex gap-4 items-center bg-black/30 p-4 rounded-lg">
                                    <div className="flex-1">
                                        <div className="text-sm text-gray-500">Next Leg</div>
                                        <div className="font-bold">Pending...</div>
                                    </div>
                                    <div className="text-2xl">⚡</div>
                                    <div className="flex-1 text-right">
                                        <div className="text-sm text-gray-500">Hedge Recommendation</div>
                                        <div className="font-bold text-blue-400">{h.recommendation}</div>
                                    </div>
                                </div>
                            </div>
                        ))
                    ) : (
                        <div className="text-center py-8 text-gray-500 font-mono bg-white/5 rounded-xl">
                            No active hedge recommendations.
                        </div>
                    )}
                  </div>

                  <div className="glass rounded-2xl p-8 border-l-4 border-yellow-500">
                    <h2 className="text-2xl font-bold mb-4 flex items-center gap-3">
                        <span className="text-3xl">💰</span> Cashout Monitor
                    </h2>
                    <p className="text-gray-400">Мониторинг предложений досрочной выплаты от букмекера.</p>
                    <div className="text-center py-8 text-gray-500 font-mono">No active cashout opportunities.</div>
                  </div>
              </section>
           )}
        </div>
      );
    }

    ReactDOM.createRoot(document.getElementById('root')).render(<App />);
  </script>
</body>
</html>
"""


def write_dashboard_html(path: str = "dashboard/index.html"):
    """Создать файл React dashboard"""
    import os
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(REACT_DASHBOARD_HTML)
    logger.info(f"Dashboard HTML written to {path}")
