#2.0.6
import flask
import requests
import threading
import time
import json
import random
from collections import Counter
from argparse import ArgumentParser

# --- 1. LÓGICA DA APLICAÇÃO (Sem alterações) ---
app = flask.Flask(__name__)

test_state = { "status": "idle", "params": {}, "live_stats": {"total": 0}, "results": [], "summary": {}, "time_series_data": [] }
state_lock = threading.Lock()

def data_aggregator():
    last_processed_index = 0
    while True:
        time.sleep(10)
        with state_lock:
            if test_state["status"] not in ["ramping", "running"]:
                last_processed_index = 0
                test_state["time_series_data"] = []
                continue
            current_results_count = len(test_state["results"])
            new_results = test_state["results"][last_processed_index:]
            last_processed_index = current_results_count
            if not new_results:
                continue
            interval_duration = 10.0
            interval_counts = Counter(categorize_result(r['status_code']) for r in new_results)
            rates = { category: f"{interval_counts.get(category, 0) / interval_duration:.2f}" for category in ['success', 'rate_limit', 'client_error', 'server_error', 'network_error'] }
            success_times = [r["duration"] for r in new_results if categorize_result(r['status_code']) == 'success']
            avg_response_time = sum(success_times) / len(success_times) if success_times else 0
            interval_data = { "timestamp": time.strftime('%H:%M:%S'), "rates": rates, "avg_response_time": f"{avg_response_time:.4f}", }
            test_state["time_series_data"].append(interval_data)

def user_simulation(params, headers):
    for _ in range(params.get("reqs_per_user", 1)):
        with state_lock:
            if test_state["status"] not in ["ramping", "running"]: break
        result = worker(params["url"], params["method"], headers, params["body"])
        with state_lock:
            if test_state["status"] in ["ramping", "running"]: test_state["results"].append(result)
        try:
            delay = random.uniform(params.get('delay_min', 0.5), params.get('delay_max', 2.0)) if params.get('delay_type') == 'variable' else params.get('delay_constant', 1.0)
            time.sleep(delay)
        except (ValueError, KeyError): time.sleep(0)

def worker(url, method, headers, body):
    start_time = time.time()
    result = {"status_code": None, "duration": 0, "error": None}
    try:
        req_body = json.loads(body) if body else None
        response = requests.request(method, url, headers=headers, json=req_body, timeout=30)
        result["status_code"] = response.status_code
    except requests.exceptions.RequestException as e: result["error"] = str(e)
    except json.JSONDecodeError as e: result["error"] = f"JSON Body Error: {e}"
    result["duration"] = time.time() - start_time
    return result

def run_load_test(params):
    threads = []
    start_time = time.time()
    with state_lock: test_state['start_time'] = start_time
    try: headers = {k.strip(): v.strip() for line in params.get("headers", "").strip().split("\n") if ":" in line for k, v in [line.split(":", 1)]}
    except Exception: headers = {}
    with state_lock: test_state["status"] = "ramping"
    users_to_start = params.get("users", 1)
    ramp_up_duration = params.get("ramp_up", 0)
    ramp_up_interval = ramp_up_duration / users_to_start if ramp_up_duration > 0 and users_to_start > 0 else 0
    for _ in range(users_to_start):
        with state_lock:
            if test_state["status"] not in ["ramping", "running"]: break
        thread = threading.Thread(target=user_simulation, args=(params, headers)); threads.append(thread); thread.start()
        time.sleep(ramp_up_interval)
    with state_lock:
        if test_state["status"] == "ramping": test_state["status"] = "running"
    for thread in threads: thread.join()
    duration = time.time() - start_time
    with state_lock:
        test_state["summary"] = calculate_summary(test_state["results"], duration); test_state["status"] = "finished"

def categorize_result(status_code):
    if status_code is None: return 'network_error'
    if 200 <= status_code < 300: return 'success'
    if status_code == 429: return 'rate_limit'
    if 400 <= status_code < 500: return 'client_error'
    if 500 <= status_code < 600: return 'server_error'
    return 'other_error'

def calculate_summary(results, duration):
    total_reqs = len(results)
    if total_reqs == 0: return {}
    categorized_counts = Counter(categorize_result(r['status_code']) for r in results)
    success_times = [r["duration"] for r in results if categorize_result(r['status_code']) == 'success']
    summary = {"total_duration": f"{duration:.2f}", "total_requests": total_reqs, "rps": f"{total_reqs / duration:.2f}" if duration > 0 else "0.00",
        "categorized_distribution": {"success": categorized_counts.get('success', 0), "rate_limit": categorized_counts.get('rate_limit', 0), "client_error": categorized_counts.get('client_error', 0), "server_error": categorized_counts.get('server_error', 0), "network_error": categorized_counts.get('network_error', 0),}}
    if success_times:
        success_times.sort()
        summary.update({"avg_response_time": f"{sum(success_times) / len(success_times):.4f}", "min_response_time": f"{min(success_times):.4f}", "max_response_time": f"{max(success_times):.4f}", "p50_median": f"{success_times[int(len(success_times) * 0.50)]:.4f}", "p95": f"{success_times[int(len(success_times) * 0.95)]:.4f}", "p99": f"{success_times[int(len(success_times) * 0.99)]:.4f}",})
    return summary

# --- 2. ROTAS FLASK (Sem alterações) ---

@app.route('/')
def index(): return flask.render_template_string(HTML_TEMPLATE)

@app.route('/healthcheck')
def health_check(): return flask.jsonify({"status": "ok"}), 200

@app.route('/start_test', methods=['POST'])
def start_test():
    with state_lock:
        if test_state["status"] in ["ramping", "running"]: return flask.jsonify({"error": "Test already running"}), 409
        form_data = flask.request.form.to_dict(); params = {}
        for key, value in form_data.items():
            try: params[key] = float(value) if '.' in value else int(value)
            except (ValueError, TypeError): params[key] = value
        test_state.update({"params": params, "status": "idle", "results": [], "summary": {}, "live_stats": {"total": 0}, "time_series_data": []})
        threading.Thread(target=run_load_test, args=(test_state["params"],)).start()
    return flask.redirect(flask.url_for('index'))

@app.route('/stop_test', methods=['POST'])
def stop_test():
    with state_lock:
        if test_state["status"] in ["ramping", "running"]: test_state["status"] = "stopping"
    return flask.redirect(flask.url_for('index'))

@app.route('/get_status')
def get_status():
    with state_lock:
        total_reqs = len(test_state["results"])
        if total_reqs > 0 and 'start_time' in test_state:
            categorized_counts = Counter(categorize_result(r['status_code']) for r in test_state["results"])
            test_state["live_stats"] = {"success": categorized_counts.get('success', 0), "errors": total_reqs - categorized_counts.get('success', 0), "total": total_reqs}
        return flask.jsonify(test_state)

# --- 3. TEMPLATE HTML (INTERFACE) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8"><title>Ferramenta de Teste de Carga</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * { box-sizing: border-box; } body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; background-color: #f8f9fa; color: #343a40; }
        .main-layout { display: grid; grid-template-columns: 450px 1fr; min-height: 100vh; }
        .left-column { padding: 20px; background-color: #fff; border-right: 1px solid #dee2e6; display: flex; flex-direction: column; gap: 20px; }
        .right-column { padding: 20px; display: flex; flex-direction: column; gap: 20px; overflow-y: auto; }
        .container { background: white; padding: 25px; border-radius: 8px; border: 1px solid #e9ecef; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }
        h1, h2, h3 { color: #003049; margin-top: 0; } h3 { margin-bottom: 20px; text-align: center; }
        label { display: block; margin-bottom: 5px; font-weight: 600; }
        input, select, textarea { width: 100%; padding: 10px; margin-bottom: 15px; border: 1px solid #ced4da; border-radius: 4px; }
        textarea { font-family: monospace; height: 80px; }
        .grid-3, .grid-2 { display: grid; gap: 15px; } .grid-3 { grid-template-columns: repeat(3, 1fr); } .grid-2 { grid-template-columns: repeat(2, 1fr); }
        .btn { padding: 12px 20px; border: none; border-radius: 5px; cursor: pointer; color: white; font-size: 16px; font-weight: bold; margin-right: 10px; }
        .btn-start { background-color: #007bff; } .btn-stop { background-color: #dc3545; } .btn:disabled { background-color: #6c757d; cursor: not-allowed; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; } th, td { text-align: left; padding: 12px; border-bottom: 1px solid #dee2e6; }
        #summary-legend { display: flex; flex-wrap: wrap; justify-content: center; gap: 15px; margin-bottom: 20px; }
        .legend-item { display: flex; align-items: center; font-size: 14px; } .legend-color-box { width: 15px; height: 15px; margin-right: 8px; border-radius: 3px; }
        .chart-wrapper { position: relative; height: 350px; width: 100%; margin: auto; } .chart-section { margin-bottom: 20px; }
    </style>
</head>
<body>
    <div class="main-layout">
        <div class="left-column">
            <h1>Ferramenta de Teste de Carga</h1>
            <div class="container">
                <h2>Configuração</h2>
                <form id="test-form" action="/start_test" method="post">
                    <label for="url">URL de Destino</label><input type="text" id="url" name="url" value="https://api.github.com/events" required>
                    <div class="grid-3">
                        <div><label for="users">Usuários</label><input type="number" id="users" name="users" value="10" min="1" required></div>
                        <div><label>Reqs/Usuário</label><input type="number" id="reqs_per_user" name="reqs_per_user" value="5" min="1" required></div>
                        <div><label>Ramp-up (s)</label><input type="number" id="ramp_up" name="ramp_up" value="5" min="0" required></div>
                    </div>
                    <label for="delay_type">Tipo de Intervalo</label><select id="delay_type" name="delay_type"><option value="constant">Constante</option><option value="variable">Variável</option></select>
                    <div id="constant-delay-div"><label for="delay_constant">Intervalo (s)</label><input type="number" id="delay_constant" name="delay_constant" value="1" min="0" step="0.1"></div>
                    <div id="variable-delay-div" style="display:none;"><div class="grid-2"><div><label>Min (s)</label><input type="number" name="delay_min" value="0.5"></div><div><label>Max (s)</label><input type="number" name="delay_max" value="2.0"></div></div></div>
                    <label for="method">Método HTTP</label><select id="method" name="method"><option value="GET">GET</option><option value="POST">POST</option><option value="PUT">PUT</option></select>
                    <div id="post-put-options" style="display:none;"><label>Cabeçalhos</label><textarea name="headers" placeholder="Content-Type: application/json"></textarea><label>Corpo (JSON)</label><textarea name="body" placeholder='{"key": "value"}'></textarea></div>
                    <button id="start-btn" type="submit" class="btn btn-start">Iniciar Teste</button><button id="stop-btn" type="button" class="btn btn-stop" style="display:none;">Parar Teste</button>
                </form>
            </div>
            <div id="summary-container" class="container" style="display:none;">
                <h2>Resumo Final do Teste</h2><table id="summary-table"></table>
            </div>
        </div>
        <div class="right-column">
            <div id="results-container" class="container" style="display:none;">
                <h2>Resultados em Tempo Real</h2>
                <p><strong>Status:</strong> <span id="status-text"></span> | <strong>Progresso:</strong> <span id="progress-text">0 / 0</span></p>
                <div class="grid-2">
                    <div style="background-color: #d4edda; color: #155724; padding: 15px; border-radius: 5px; text-align: center;"><strong>Sucessos:</strong> <span id="live-success-count">0</span></div>
                    <div style="background-color: #f8d7da; color: #721c24; padding: 15px; border-radius: 5px; text-align: center;"><strong>Erros:</strong> <span id="live-error-count">0</span></div>
                </div>
            </div>
            <div id="charts-container" class="container" style="display:none;">
                <h2>Gráficos de Performance</h2>
                <div class="chart-section"><h3 style="margin-bottom: 20px;">Requisições por Segundo (RPS)</h3><div class="chart-wrapper"><canvas id="rps-chart"></canvas></div></div>
                <div class="chart-section"><h3 style="margin-bottom: 20px;">Tempo de Resposta Médio (Sucessos)</h3><div class="chart-wrapper"><canvas id="response-time-chart"></canvas></div></div>
                <div class="chart-section"><h3 style="margin-bottom: 5px;">Distribuição de Respostas (Final)</h3><div id="summary-legend"><span class="legend-item"><div class="legend-color-box" style="background-color: rgba(40, 167, 69, 0.8);"></div>Sucesso (2xx)</span><span class="legend-item"><div class="legend-color-box" style="background-color: rgba(108, 92, 231, 0.8);"></div>Rate Limit (429)</span><span class="legend-item"><div class="legend-color-box" style="background-color: rgba(255, 193, 7, 0.8);"></div>Erro Cliente (4xx)</span><span class="legend-item"><div class="legend-color-box" style="background-color: rgba(220, 53, 69, 0.8);"></div>Erro Servidor (5xx)</span><span class="legend-item"><div class="legend-color-box" style="background-color: rgba(108, 117, 125, 0.8);"></div>Erro Rede/Timeout</span></div><div class="chart-wrapper"><canvas id="summary-chart"></canvas></div></div>
            </div>
        </div>
    </div>
<script>
    const startBtn = document.getElementById('start-btn'), stopBtn = document.getElementById('stop-btn'), testForm = document.getElementById('test-form');
    const resultsContainer = document.getElementById('results-container'), summaryContainer = document.getElementById('summary-container'), chartsContainer = document.getElementById('charts-container');
    const statusText = document.getElementById('status-text'), progressText = document.getElementById('progress-text');
    const liveSuccessCount = document.getElementById('live-success-count'), liveErrorCount = document.getElementById('live-error-count');
    const summaryTable = document.getElementById('summary-table');
    let statusInterval, summaryChart, rpsChart, responseTimeChart;
    const chartConfigs = {
        keys: ['success', 'rate_limit', 'client_error', 'server_error', 'network_error'],
        colors: { success: 'rgba(40, 167, 69, 0.7)', rate_limit: 'rgba(108, 92, 231, 0.7)', client_error: 'rgba(255, 193, 7, 0.7)', server_error: 'rgba(220, 53, 69, 0.7)', network_error: 'rgba(108, 117, 125, 0.7)' },
        labels: { success: 'Sucesso (2xx)', rate_limit: 'Rate Limit (429)', client_error: 'Erro Cliente (4xx)', server_error: 'Erro Servidor (5xx)', network_error: 'Erro Rede/Timeout' }
    };
    document.getElementById('delay_type').addEventListener('change', e => { document.getElementById('constant-delay-div').style.display = e.target.value === 'constant' ? 'block' : 'none'; document.getElementById('variable-delay-div').style.display = e.target.value === 'variable' ? 'block' : 'none'; });
    document.getElementById('method').addEventListener('change', e => { document.getElementById('post-put-options').style.display = ['POST', 'PUT'].includes(e.target.value) ? 'block' : 'none'; });
    document.getElementById('delay_type').dispatchEvent(new Event('change'));
    stopBtn.addEventListener('click', () => fetch('/stop_test', { method: 'POST' }));
    testForm.addEventListener('submit', (e) => { e.preventDefault(); fetch('/start_test', { method: 'POST', body: new FormData(testForm) }).then(res => res.ok && startMonitoring()); });
    
    function startMonitoring() {
        startBtn.disabled = true; stopBtn.style.display = 'inline-block';
        resultsContainer.style.display = 'block'; summaryContainer.style.display = 'none';
        summaryTable.innerHTML = ''; chartsContainer.style.display = 'block';
        
        // ### PONTO-CHAVE ###
        // Os gráficos são limpos e reinicializados AQUI, e somente aqui,
        // garantindo que os dados do teste anterior sejam removidos apenas
        // quando um NOVO teste é iniciado.
        initializeCharts();
        
        statusInterval = setInterval(updateStatus, 5000);
    }

    function initializeCharts() {
        if(summaryChart) summaryChart.destroy(); if(rpsChart) rpsChart.destroy(); if(responseTimeChart) responseTimeChart.destroy();

        summaryChart = new Chart(document.getElementById('summary-chart'), { type: 'doughnut', data: { labels: Object.values(chartConfigs.labels), datasets: [{ data: [], backgroundColor: Object.values(chartConfigs.colors), borderWidth: 0 }] }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } } });
        
        const rpsDatasets = chartConfigs.keys.map(key => ({
            label: chartConfigs.labels[key], data: [], backgroundColor: chartConfigs.colors[key],
            borderColor: chartConfigs.colors[key], fill: true, key: key, pointRadius: 2, tension: 0.3
        }));
        rpsChart = new Chart(document.getElementById('rps-chart'), {
            type: 'line', data: { labels: [], datasets: rpsDatasets },
            options: { responsive: true, maintainAspectRatio: false,
                scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true, title: { display: true, text: 'Reqs/seg' } } },
                animation: false, interaction: { mode: 'index', intersect: false }, plugins: { legend: { position: 'top' } }
            }
        });
        
        responseTimeChart = new Chart(document.getElementById('response-time-chart'), { type: 'line', data: { labels: [], datasets: [{ label: 'Tempo Médio (s)', data: [], borderColor: '#28a745', tension: 0.3, fill: false, pointRadius: 2 }] }, options: { responsive: true, maintainAspectRatio: false, scales: { y: { beginAtZero: true, title: { display: true, text: 'Segundos' } } }, animation: false } });
    }

    function updateStatus() {
        fetch('/get_status').then(res => res.json()).then(data => {
            statusText.textContent = data.status.charAt(0).toUpperCase() + data.status.slice(1);
            const target = (data.params.users || 0) * (data.params.reqs_per_user || 0);
            progressText.textContent = `${data.live_stats.total || 0} / ${target}`;
            liveSuccessCount.textContent = data.live_stats.success || 0;
            liveErrorCount.textContent = data.live_stats.errors || 0;

            if (data.time_series_data) {
                const labels = data.time_series_data.map(d => d.timestamp);
                rpsChart.data.labels = labels;
                rpsChart.data.datasets.forEach(dataset => { dataset.data = data.time_series_data.map(d => d.rates[dataset.key] || 0); });
                rpsChart.update();
                responseTimeChart.data.labels = labels;
                responseTimeChart.data.datasets[0].data = data.time_series_data.map(d => d.avg_response_time);
                responseTimeChart.update();
            }

            if (data.status === 'finished') {
                // ### PONTO-CHAVE ###
                // Ao finalizar, a atualização é parada. Nenhum comando para limpar os
                // gráficos de linha é chamado aqui. Eles permanecem na tela com os
                // últimos dados recebidos, como solicitado.
                clearInterval(statusInterval);
                startBtn.disabled = false; stopBtn.style.display = 'none';
                displaySummary(data.summary);
            }
        });
    }

    function displaySummary(summary) {
        resultsContainer.style.display = 'none'; summaryContainer.style.display = 'block';
        if (!summary || Object.keys(summary).length === 0) { summaryTable.innerHTML = '<tr><td>Nenhum resultado para exibir.</td></tr>'; return; }
        let html = `<tr><td>Duração Total</td><td>${summary.total_duration}s</td></tr><tr><td>Total de Requisições</td><td>${summary.total_requests}</td></tr><tr><td>RPS (Média)</td><td>${summary.rps}</td></tr><tr><td colspan="2" style="background-color:#f2f2f2;"><strong>Estatísticas de Resposta (sucessos)</strong></td></tr><tr><td>Tempo Médio</td><td>${summary.avg_response_time || 'N/A'}s</td></tr><tr><td>Tempo Mínimo</td><td>${summary.min_response_time || 'N/A'}s</td></tr><tr><td>Tempo Máximo</td><td>${summary.max_response_time || 'N/A'}s</td></tr><tr><td>Mediana (p50)</td><td>${summary.p50_median || 'N/A'}s</td></tr><tr><td>Percentil 95 (p95)</td><td>${summary.p95 || 'N/A'}s</td></tr>`;
        summaryTable.innerHTML = html;
        if (summary.categorized_distribution) {
            const dist = summary.categorized_distribution;
            summaryChart.data.datasets[0].data = chartConfigs.keys.map(key => dist[key] || 0);
            summaryChart.update();
        }
    }
</script>
</body>
</html>
"""

# --- 4. BLOCO DE EXECUÇÃO PRINCIPAL (Sem alterações) ---
if __name__ == '__main__':
    parser = ArgumentParser(); parser.add_argument('--host', default='127.0.0.1', help='Host a ser vinculado (ex: 0.0.0.0)'); parser.add_argument('--port', default=5000, type=int, help='Porta para escutar'); args = parser.parse_args()
    aggregator_thread = threading.Thread(target=data_aggregator, daemon=True); aggregator_thread.start()
    app.run(host=args.host, port=args.port, debug=False)
