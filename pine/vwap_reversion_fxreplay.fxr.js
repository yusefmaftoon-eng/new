//@version=1
// FXR Script (FXReplay's custom-indicator language -- NOT Pine Script, confirmed via
// their docs at https://custom-indicators.gitbook.io/custom-indicators-docs) port of
// strategies/vwap_reversion_strategy.py.
//
// IMPORTANT: FXR Script has no order-placement / strategy-execution API at all (checked
// their full 195-page doc index -- every section is plotting/drawing tools, script inputs,
// or the ta-math/moment libraries; the one function that sounded like it might place a
// trade, longPosition(), is confirmed to be a purely visual risk-reward box with no P&L
// tracking behind it). So this CANNOT reproduce the automated backtest the Pine Script
// version or the Python backtest give you -- no equity curve, win rate, or profit factor.
// What it CAN do: draw the VWAP + bands and mark each qualifying entry (with the stop/
// target it would have used) as an arrow on the chart, so you can manually execute and
// journal it while stepping through FXReplay's replay mode.
//
// Two things I could not verify against FXR Script's documentation and had to make a
// judgment call on -- check these first if the script errors or looks wrong:
//   1. volume(n) as the bar-volume accessor. Confirmed function names from their own
//      examples: high(n), low(n), closeC(n), openC(n), time(n) (n = bars back, 0 = current).
//      Volume wasn't shown in any example I could pull; volume(n) is the most likely name
//      by pattern-matching the others, not a confirmed one.
//   2. Session/timezone handling. FXR Script's moment library (_moment) has no documented
//      America/New_York conversion -- only .utc(), .format(), .date() are confirmed
//      available. sessionStartHour/sessionEndHour below are therefore in UTC, defaulted to
//      13.5/20.0 = 09:30-16:00 ET *during EDT* (UTC-4). When the US switches to standard
//      time (~early November), these need to shift to 14.5/21.0 (UTC-5) or the window will
//      be off by an hour. There's no way I found to make this self-adjusting from the docs.

init = () => {
  indicator({ onMainPanel: true, format: 'inherit' });
  input.float('Entry band (std mult)', 2.0, 'bandMult');
  input.float('Stop band (std mult, must be > entry band)', 3.0, 'stopMult');
  input.int('Rolling std window (bars)', 60, 'stdWindow');
  input.float('Session start hour (UTC)', 13.5, 'sessionStart');
  input.float('Session end hour (UTC)', 20.0, 'sessionEnd');
  input.int('Minimum stop distance (ticks-equivalent, in price units x100)', 25, 'minStopUnits');
};

// Reset daily -- feeds ta.vwap() so it's a SESSION vwap, not a running-forever one.
const dayHigh = [];
const dayLow = [];
const dayClose = [];
const dayVol = [];
// NOT reset daily -- matches the Python source's rolling 60-bar std of (close - vwap),
// which spans across day boundaries.
const devHistory = [];
let lastDay = null;

onTick = (length, _moment, _, ta, inputs) => {
  const bandMult = inputs.bandMult;
  const stopMult = inputs.stopMult;
  const stdWindow = inputs.stdWindow;
  const sessionStart = inputs.sessionStart;
  const sessionEnd = inputs.sessionEnd;
  const minStopUnits = inputs.minStopUnits / 100;

  const o = openC(0), h = high(0), l = low(0), c = closeC(0), v = volume(0);
  if ([o, h, l, c, v].some(x => isNaN(x))) return;

  const m = _moment.utc(time(0));
  const day = m.date();
  const hourFloat = Number(m.format('HH')) + Number(m.format('mm')) / 60;

  if (lastDay !== null && day !== lastDay) {
    dayHigh.length = 0; dayLow.length = 0; dayClose.length = 0; dayVol.length = 0;
  }
  lastDay = day;
  dayHigh.push(h); dayLow.push(l); dayClose.push(c); dayVol.push(v);

  const vwapNow = ta.vwap(dayHigh, dayLow, dayClose, dayVol).at(-1);
  plot.line('VWAP', vwapNow, '#FFD700', 2);

  const dev = c - vwapNow;
  devHistory.push(dev);
  if (devHistory.length > stdWindow) devHistory.shift();
  if (devHistory.length < stdWindow) return; // building up the rolling window

  const std = ta.stdev(devHistory, stdWindow).at(-1);
  if (!std || isNaN(std)) return;

  const band = bandMult * std;
  const stopBand = stopMult * std;
  plot.line('Upper band', vwapNow + band, 'rgba(255,0,0,0.5)', 1);
  plot.line('Lower band', vwapNow - band, 'rgba(0,255,0,0.5)', 1);

  const inSession = hourFloat >= sessionStart && hourFloat < sessionEnd;
  if (!inSession || devHistory.length < 2) return;

  // Compares the PRIOR bar's dev against the CURRENT bar's band (not each bar's own
  // contemporaneous band) -- matches the Python source exactly, same reasoning as the
  // Pine Script port: this deliberately isn't a plain crossover check.
  const prevDev = devHistory[devHistory.length - 2];

  if (prevDev <= band && dev > band) {
    const stop = vwapNow + stopBand;
    if (stop > c && (stop - c) >= minStopUnits) {
      arrowDown(time(0), h, { arrowColor: color.red, color: color.white, fontsize: 11, bold: true, showLabel: true },
        `SHORT stop ${stop.toFixed(2)} target ${vwapNow.toFixed(2)}`);
    }
  } else if (prevDev >= -band && dev < -band) {
    const stop = vwapNow - stopBand;
    if (stop < c && (c - stop) >= minStopUnits) {
      arrowUp(time(0), l, { arrowColor: color.green, color: color.white, fontsize: 11, bold: true, showLabel: true },
        `LONG stop ${stop.toFixed(2)} target ${vwapNow.toFixed(2)}`);
    }
  }
};
