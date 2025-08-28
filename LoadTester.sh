#!/bin/bash
set -e
   
LOG_PREFIX="[SETUP-SCRIPT]"
ENV_FILE="/home/ec2-user/.env"

log() {
    echo "${LOG_PREFIX} $1"
}

# --- 1. Carregamento das Variáveis de Ambiente ---
log "Carregando variáveis de ambiente do arquivo ${ENV_FILE}..."

if [ ! -f "${ENV_FILE}" ]; then
    log "ERRO: Arquivo de ambiente ${ENV_FILE} não encontrado."
    exit 1
fi

# 'source' (ou '.') carrega as variáveis do arquivo para a sessão atual.
# 'set -a' exporta automaticamente todas as variáveis carregadas.
set -a 
source "${ENV_FILE}"
set +a

# --- 2. Instalação de Dependências ---
log "Iniciando a instalação de dependências..."
#sudo yum update -y
sudo yum install -y python3 python3-pip aws-cli

# CORREÇÃO 1: Força a instalação de uma versão do urllib3 compatível com o botocore (<2.0)
# para resolver o conflito de dependência.
pip3 install boto3 flask requests "urllib3<2.0"

# --- 3. Download do Script Python do S3 ---
log "Baixando o script '${AWS_S3_PYTHON_KEY}' do bucket S3 '${AWS_S3_BUCKET_TARGET_NAME_SCRIPT}'..."

cd /opt
# Usa as variáveis carregadas do arquivo .env
aws s3 cp "s3://${AWS_S3_BUCKET_TARGET_NAME_SCRIPT}/${AWS_S3_PYTHON_KEY}" .

# --- 4. Criação do Serviço systemd com as Variáveis ---
log "Criando o serviço systemd para a aplicação..."

# CORREÇÃO 2: Usando '<<-EOF' (com hífen) para permitir que a tag de fechamento 'EOF'
# seja indentada, o que corrige o erro de "here-document" e "Missing '='".
cat <<-EOF | sudo tee /etc/systemd/system/loadtester.service
[Unit]
Description=Load Tester Flask Application
After=network.target

[Service]
User=ec2-user
Group=ec2-user
WorkingDirectory=/opt

# O systemd carrega o arquivo .env antes de rodar o comando.
EnvironmentFile=${ENV_FILE}

# O comando para iniciar a aplicação. Note o uso de ${AWS_S3_PYTHON_KEY}
# que será expandido pelo systemd após ler o EnvironmentFile.
ExecStart=/usr/bin/python3 ${AWS_S3_PYTHON_KEY} --host=0.0.0.0

Restart=always

[Install]
WantedBy=multi-user.target
EOF

# --- 5. Habilitação e Início do Serviço ---
log "Habilitando e iniciando o serviço loadtester..."
sudo systemctl daemon-reload
sudo systemctl enable loadtester.service
sudo systemctl start loadtester.service

log "Configuração da instância finalizada com sucesso."
