#!/usr/bin/env python3
"""
MT4 最適化ランチャー GUI

mt4_optimizer_2.py の -o (操作モード) / -k (EA) / --symbol / --period /
--from-date / --to-date をウィンドウから選択・入力して実行する。
実行中は標準出力をログ欄にリアルタイム表示する。

使用方法:
  python optimizer_gui.py
"""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

TOOLS_DIR   = Path(__file__).parent
CONFIGS_DIR = TOOLS_DIR / "configs"
RESULTS_DIR = TOOLS_DIR / "results"
OPTIMIZER   = TOOLS_DIR / "mt4_optimizer_2.py"

sys.path.insert(0, str(TOOLS_DIR))
import mt4_optimizer_2 as opt2  # noqa: E402  (定数・デフォルト値の再利用のみ。副作用なし)

PERIOD_LABELS = {
    '1': 'M1', '5': 'M5', '15': 'M15', '30': 'M30',
    '60': 'H1', '240': 'H4', '1440': 'D1',
}
PERIOD_KEYS = list(opt2.PERIOD_INDEX_MAP.keys())

# (value, title, requires_best_params, description)
OPERATIONS = [
    ('evolve', 'evolve', False,
     '遺伝的アルゴリズムで全パラメータを最適化。50個体×最大100世代。各世代の上位10体を'
     'そのまま継承（エリート）、上位20体から交叉で30体生成、残り10体はランダム新規個体として'
     '多様性を確保。8世代ベスト更新がなければ早期終了。evolve_checkpoint.json があれば'
     '続きから自動再開し、結果は best_params.json に保存。'),
    ('grid', 'grid', False,
     '全パラメータ組み合わせを総当りで評価。組み合わせ数が100を超える場合は固定シードで'
     'ランダムに100件だけサンプリングして実行。GAのようなランダム性による偏りが少なく、'
     'ざっくり全体像を掴みたい時向き。'),
    ('refine', 'refine', True,
     'ベストパラメータ周辺だけを細かく再探索。各パラメータをグリッド上でベスト値の前後1'
     'ステップに絞り、数値パラメータはさらに中間値も追加した狭いグリッドでGA（最大40世代）'
     'を実行。evolveで大まかな当たりを付けた後の仕上げ用。'),
    ('adaptive', 'adaptive', True,
     '連敗時の自動休止パラメータだけを最適化。ベストパラメータに AdaptiveWindow(3値) × '
     'AdaptivePauseWR(4値) × AdaptivePauseHours(3値) = 27通りを重ねて総当り。適応停止なしの'
     'ベースラインと比較して、休止ロジックが実際に効果があるかを検証する。'),
    ('atr', 'atr', True,
     'ATRフィルターパラメータだけを最適化。ベストパラメータに ATR_Short × ATR_Baseline × '
     'ATR_Multiplier = 27通りを重ねて総当り。フィルターなしのベースラインと比較し、'
     'ボラティリティ急変時のエントリー禁止フィルターの効果を検証する。'),
    ('ablate', 'ablate', True,
     '各パラメータの因果的な寄与度を測定 (Leave-one-out)。best_params.json の値を1つだけ'
     'グリッド中央値に戻し、他は固定したまま再テスト。これをパラメータの数だけ繰り返し、'
     'スコアの低下幅が大きいほど「そのパラメータの最適化が効いている」と判定できる。'),
    ('backtest', 'backtest', True,
     '最終確認用の精密バックテストを1回だけ実行。Control Points モデルによる精度優先の'
     '判定を行う。他モードと違い MT4 を終了せず開いたままにするため、Results/Graph タブで'
     '損益カーブなどを目視確認できる。'),
]

LOG_TAGS = {
    'ok':    dict(foreground='#2e7d46'),
    'warn':  dict(foreground='#b3720b'),
    'err':   dict(foreground='#c22b2b'),
    'dim':   dict(foreground='#7d848f'),
}


def _list_ea_keys():
    if not CONFIGS_DIR.exists():
        return []
    return sorted(p.stem for p in CONFIGS_DIR.glob('*.json'))


def _load_ea_defaults(key):
    """configs/<key>.json から symbol/period のデフォルト値を読む"""
    cfg_path = CONFIGS_DIR / f'{key}.json'
    if not cfg_path.exists():
        return opt2.TESTER_SYMBOL, opt2.TESTER_PERIOD
    import json
    cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
    symbol = cfg.get('symbol', opt2.TESTER_SYMBOL)
    period = str(cfg.get('period', opt2.TESTER_PERIOD))
    return symbol, period


class OptimizerGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('MT4 最適化ランチャー')
        self.geometry('900x760')
        self.minsize(820, 640)

        self.proc = None
        self.log_queue: queue.Queue = queue.Queue()
        self.operation_var = tk.StringVar(value=OPERATIONS[0][0])
        self.ea_var = tk.StringVar()
        self.symbol_var = tk.StringVar()
        self.period_var = tk.StringVar()
        self.from_var = tk.StringVar()
        self.to_var = tk.StringVar()

        self._build_widgets()
        self._refresh_ea_list()
        self.after(150, self._poll_log_queue)

    # ---------- ウィジェット構築 ----------

    def _build_widgets(self):
        pad = dict(padx=10, pady=6)

        top = ttk.Frame(self)
        top.pack(fill='x', **pad)
        ttk.Label(top, text='EA (-k)', font=('', 9, 'bold')).pack(side='left')
        self.ea_combo = ttk.Combobox(top, textvariable=self.ea_var, state='readonly', width=28)
        self.ea_combo.pack(side='left', padx=(8, 0))
        self.ea_combo.bind('<<ComboboxSelected>>', lambda e: self._on_ea_changed())
        ttk.Button(top, text='再読込', command=self._refresh_ea_list).pack(side='left', padx=(8, 0))

        cond = ttk.LabelFrame(self, text='テスト条件 (この回のみ上書き。ファイルは書き換えない)')
        cond.pack(fill='x', **pad)

        ttk.Label(cond, text='通貨 (symbol)').grid(row=0, column=0, sticky='w', padx=8, pady=6)
        ttk.Entry(cond, textvariable=self.symbol_var, width=14).grid(row=0, column=1, padx=(0, 20))

        ttk.Label(cond, text='足 (period)').grid(row=0, column=2, sticky='w')
        period_display = [f'{PERIOD_LABELS.get(k, k)} ({k})' for k in PERIOD_KEYS]
        self.period_display_var = tk.StringVar()
        self.period_combo = ttk.Combobox(cond, textvariable=self.period_display_var,
                                          values=period_display, state='readonly', width=10)
        self.period_combo.grid(row=0, column=3, padx=(8, 20))
        self.period_combo.bind('<<ComboboxSelected>>', lambda e: self._on_period_display_changed())

        ttk.Label(cond, text='開始日').grid(row=0, column=4, sticky='w')
        ttk.Entry(cond, textvariable=self.from_var, width=12).grid(row=0, column=5, padx=(8, 20))

        ttk.Label(cond, text='終了日').grid(row=0, column=6, sticky='w')
        ttk.Entry(cond, textvariable=self.to_var, width=12).grid(row=0, column=7, padx=(8, 20))

        ttk.Button(cond, text='↺ configの初期値に戻す',
                   command=self._reset_conditions).grid(row=0, column=8, padx=8)

        op_frame = ttk.LabelFrame(self, text='操作モード (-o)')
        op_frame.pack(fill='both', expand=False, **pad)

        for value, title, requires, desc in OPERATIONS:
            row = ttk.Frame(op_frame)
            row.pack(fill='x', padx=8, pady=4)
            head = ttk.Frame(row)
            head.pack(fill='x')
            ttk.Radiobutton(head, text=title, variable=self.operation_var, value=value,
                             command=self._update_requirement_hint).pack(side='left')
            need_lbl = ttk.Label(head, text='要 best_params.json' if requires else '初回OK',
                                  foreground='#b3720b' if requires else '#7d848f',
                                  font=('', 8, 'bold'))
            need_lbl.pack(side='right')
            desc_lbl = ttk.Label(row, text=desc, wraplength=830, foreground='#605e5c',
                                  font=('', 8), justify='left')
            desc_lbl.pack(fill='x', padx=(24, 0))

        ctrl = ttk.Frame(self)
        ctrl.pack(fill='x', **pad)
        self.run_btn = ttk.Button(ctrl, text='▶ 実行', command=self._on_run)
        self.run_btn.pack(side='left')
        self.stop_btn = ttk.Button(ctrl, text='■ 停止', command=self._on_stop, state='disabled')
        self.stop_btn.pack(side='left', padx=(8, 0))
        self.status_var = tk.StringVar(value='待機中')
        ttk.Label(ctrl, textvariable=self.status_var, font=('', 9, 'bold')).pack(side='right')
        self.requirement_var = tk.StringVar(value='')
        ttk.Label(ctrl, textvariable=self.requirement_var, foreground='#c22b2b').pack(side='right', padx=(0, 16))

        log_frame = ttk.LabelFrame(self, text='実行ログ')
        log_frame.pack(fill='both', expand=True, **pad)
        self.log_text = tk.Text(log_frame, bg='#111318', fg='#d7dbe0', insertbackground='#d7dbe0',
                                 font=('Consolas', 9), wrap='word', state='disabled')
        self.log_text.pack(side='left', fill='both', expand=True)
        for tag, cfg in LOG_TAGS.items():
            self.log_text.tag_configure(tag, **cfg)
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scroll.pack(side='right', fill='y')
        self.log_text['yscrollcommand'] = scroll.set

        self.footer_var = tk.StringVar(value='')
        ttk.Label(self, textvariable=self.footer_var, foreground='#605e5c',
                  font=('', 8)).pack(fill='x', padx=10, pady=(0, 8))

    # ---------- EA / テスト条件 ----------

    def _refresh_ea_list(self):
        keys = _list_ea_keys()
        self.ea_combo['values'] = keys
        if keys:
            current = self.ea_var.get()
            self.ea_var.set(current if current in keys else keys[0])
            self._on_ea_changed()
        else:
            self.ea_var.set('')

    def _on_ea_changed(self):
        self._reset_conditions()
        self._update_requirement_hint()

    def _reset_conditions(self):
        key = self.ea_var.get()
        if not key:
            return
        symbol, period = _load_ea_defaults(key)
        self.symbol_var.set(symbol)
        self.period_var.set(period)
        self.period_display_var.set(f'{PERIOD_LABELS.get(period, period)} ({period})')
        self.from_var.set(opt2.TESTER_FROM)
        self.to_var.set(opt2.TESTER_TO)

    def _on_period_display_changed(self):
        text = self.period_display_var.get()
        key = text.split('(')[-1].rstrip(')') if '(' in text else text
        self.period_var.set(key)

    def _update_requirement_hint(self):
        key = self.ea_var.get()
        op = self.operation_var.get()
        requires = next((r for v, _, r, _ in OPERATIONS if v == op), False)
        if not requires or not key:
            self.requirement_var.set('')
            return
        best_path = RESULTS_DIR / key / 'best_params.json'
        if not best_path.exists():
            self.requirement_var.set(f'⚠ {best_path} が見つかりません（先に evolve/grid を実行してください）')
        else:
            self.requirement_var.set('')

    # ---------- 実行 / 停止 ----------

    def _on_run(self):
        key = self.ea_var.get()
        if not key:
            self.status_var.set('EA が選択されていません')
            return
        if self.proc is not None:
            return

        cmd = [
            sys.executable, str(OPTIMIZER),
            '-o', self.operation_var.get(),
            '-k', key,
            '--symbol', self.symbol_var.get(),
            '--period', self.period_var.get(),
            '--from-date', self.from_var.get(),
            '--to-date', self.to_var.get(),
        ]

        self._clear_log()
        self._append_log(f'$ {" ".join(cmd)}\n', 'dim')

        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

        try:
            self.proc = subprocess.Popen(
                cmd, cwd=str(TOOLS_DIR), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace',
                bufsize=1, creationflags=creationflags,
            )
        except Exception as e:
            self._append_log(f'[エラー] 起動に失敗しました: {e}\n', 'err')
            return

        self.run_btn['state'] = 'disabled'
        self.stop_btn['state'] = 'normal'
        self.ea_combo['state'] = 'disabled'
        self.status_var.set(f'実行中: {self.operation_var.get()}')

        threading.Thread(target=self._read_process_output, daemon=True).start()

    def _on_stop(self):
        if self.proc is None:
            return
        self._append_log('\n[GUI] 停止要求を送信します（MT4 は自動で閉じないため手動確認してください）\n', 'warn')
        try:
            self.proc.terminate()
        except Exception:
            pass

    def _read_process_output(self):
        proc = self.proc
        try:
            for line in proc.stdout:
                self.log_queue.put(line)
        finally:
            proc.stdout.close()
            code = proc.wait()
            self.log_queue.put(('__DONE__', code))

    # ---------- ログ表示 ----------

    def _poll_log_queue(self):
        try:
            while True:
                item = self.log_queue.get_nowait()
                if isinstance(item, tuple) and item[0] == '__DONE__':
                    self._on_process_done(item[1])
                else:
                    self._append_log(item, self._classify(item))
        except queue.Empty:
            pass
        self.after(150, self._poll_log_queue)

    @staticmethod
    def _classify(line: str) -> str:
        if '★' in line:
            return 'ok'
        if '警告' in line or '改善なし' in line:
            return 'warn'
        if 'エラー' in line or 'Error' in line or 'Traceback' in line:
            return 'err'
        return ''

    def _append_log(self, text, tag=''):
        self.log_text['state'] = 'normal'
        if tag:
            self.log_text.insert('end', text, tag)
        else:
            self.log_text.insert('end', text)
        self.log_text.see('end')
        self.log_text['state'] = 'disabled'

    def _clear_log(self):
        self.log_text['state'] = 'normal'
        self.log_text.delete('1.0', 'end')
        self.log_text['state'] = 'disabled'

    def _on_process_done(self, returncode):
        self.proc = None
        self.run_btn['state'] = 'normal'
        self.stop_btn['state'] = 'disabled'
        self.ea_combo['state'] = 'readonly'
        ok = returncode == 0
        self._append_log(f'\n[GUI] プロセス終了 (code={returncode})\n', 'ok' if ok else 'err')
        self.status_var.set('完了' if ok else f'異常終了 (code={returncode})')

        key = self.ea_var.get()
        best_path = RESULTS_DIR / key / 'best_params.json'
        if best_path.exists():
            self.footer_var.set(f'best_params.json: {best_path}')
        self._update_requirement_hint()


if __name__ == '__main__':
    app = OptimizerGUI()
    app.mainloop()
