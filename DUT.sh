# Script UserData para configurar e rodar a aplicação de teste de carga.
# Este script assume que um arquivo /home/ec2-user/.env já foi criado.

# Carrega as variáveis de ambiente do arquivo .env para a sessão atual do shell
if [ -f /home/ec2-user/.env ]; then
    echo "Arquivo .env encontrado. Carregando variáveis..."
    source /home/ec2-user/.env
else
    echo "AVISO: Arquivo /home/ec2-user/.env não encontrado."
fi

# Atualiza os pacotes e instala dependências
yum update -y
yum install -y python3 python3-pip

# Instala a biblioteca 'requests' do Python
pip3 install requests

# Navega para o diretório do usuário
cd /home/ec2-user

# Cria o arquivo da aplicação Python
cat <<EOF > stress_app.py
# --- Cole AQUI todo o código do stress_app.py atualizado (do Passo 1) ---
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
import hashlib
import time
import requests
import os

def burn_cpu(iterations):
    s = b"string_para_gerar_hash_e_consumir_cpu"
    for _ in range(iterations):
        s = hashlib.sha256(s).digest()
    return s

def get_instance_id():
    try:
        response = requests.get('http://169.254.169.254/latest/meta-data/instance-id', timeout=0.5)
        return response.text
    except requests.exceptions.RequestException:
        return "N/A (não é uma instância EC2 ou metadados inacessíveis)"

class SimpleWebServer(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            base_iterations = int(os.environ.get('CPU_STRESS_ITERATIONS', 500000))
        except ValueError:
            base_iterations = 500000

        parsed_path = urlparse(self.path)
        query_params = parse_qs(parsed_path.query)
        try:
            iterations = int(query_params.get('iter', [base_iterations])[0])
            source = "URL parameter" if 'iter' in query_params else "Environment Variable"
        except (ValueError, TypeError):
            iterations = base_iterations
            source = "Environment Variable (fallback)"

        start_time = time.time()
        burn_cpu(iterations)
        end_time = time.time()
        duration = end_time - start_time
        
        instance_id = get_instance_id()
        
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        
        message = f"""
        <html><head><title>Teste de Carga CPU</title></head>
        <body><h1>Requisição Processada!</h1>
        <p>Esta resposta foi gerada pela instância: <b>{instance_id}</b></p>
        <p>A tarefa de CPU foi executada com <b>{iterations:,}</b> iterações.</p>
        <p><i>(Valor definido via: {source})</i></p>
        <p>Tempo de processamento: {duration:.4f} segundos.</p>
        </body></html>
        """
        self.wfile.write(bytes(message, "utf8"))

def run(server_class=HTTPServer, handler_class=SimpleWebServer, port=8000):
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    print(f"Servidor iniciado na porta {port}...")
    httpd.serve_forever()

if __name__ == "__main__":
    run()
EOF

# Define o dono do arquivo
chown ec2-user:ec2-user stress_app.py

# Roda o servidor Python em background
# As variáveis carregadas pelo 'source' estarão disponíveis para este processo
nohup python3 /home/ec2-user/stress_app.py > /dev/null 2>&1 &
