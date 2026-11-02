# B3 Monitor

Aplicação web pessoal, em pt-BR, para acompanhar ações da B3. Inclui autenticação local, carteira, watchlist, alertas, gráficos, análise por regras explícitas e relatórios diários. Não há dados de demonstração: quando a fonte está indisponível, a tela informa a falha em vez de fabricar cotações.

## Requisitos

- Python 3.11 ou superior.
- Acesso à internet para consultar brapi.dev/Banco Central e, opcionalmente, Yahoo Finance.
- Token brapi.dev opcional, conforme os limites da conta/provedor.

## Instalação e execução (PowerShell)

Na pasta do projeto:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Copie o segredo gerado para `SECRET_KEY` em `.env`. O servidor se recusa a iniciar com a chave de exemplo ou ausente. Configure `BRAPI_TOKEN` se tiver um token e então inicie:

```powershell
uvicorn main:app --reload
```

Abra http://127.0.0.1:8000. O primeiro início cria o banco SQLite local e o usuário `admin` com senha provisória `admin`. Entre e altere a senha em **Configurações**. Senhas são armazenadas com bcrypt; a sessão é um cookie JWT `httpOnly`, `SameSite=Lax`, protegido com `Secure` ao acessar por HTTPS. Tentativas malsucedidas são limitadas por endereço IP em memória.

O SQLite é salvo como `b3_monitor.db` na pasta atual por padrão. Para outro caminho, ajuste `DATABASE_URL` no `.env`. A documentação interativa das rotas fica em http://127.0.0.1:8000/docs.

## Fonte de dados

- `MARKET_DATA_PROVIDER=brapi` usa brapi.dev para cotações, histórico e fundamentos. `BRAPI_TOKEN` permanece no backend; nunca é enviado ao navegador. A lista pública de mercado pode mostrar um resumo sem token, mas consultas detalhadas e múltiplos ativos dependem das permissões/limites da conta. Configure um token para carteira, watchlist e fundamentos; respostas 401/429 são descritas sem expor a chave.
- `MARKET_DATA_PROVIDER=yfinance` seleciona Yahoo Finance. Configure `YFINANCE_TICKERS` (lista separada por vírgula) para habilitar o painel de maiores altas e quedas nesse modo.
- `MARKET_POLL_SECONDS` controla a consulta do WebSocket (padrão 60 s; mínimo operacional 15 s). A frequência efetiva depende da fonte e do plano de acesso.
- Quando a consulta falha, a última cotação brapi em cache pode ser mostrada como atrasada, com a idade do cache. Se não existir cache utilizável, a interface sinaliza indisponibilidade.

O calendário combina feriados nacionais/paulistas e os fechamentos móveis da B3 (Carnaval e Corpus Christi). Fechamentos extraordinários podem ser adicionados, como datas `AAAA-MM-DD`, em `config/b3_holidays.json`.

## Uso por etapa

1. **Login e layout:** entre com `admin` / `admin`, confirme o aviso fixo e altere a senha em Configurações. O menu lateral é recolhível; use a mesma execução do servidor.
2. **Carteira e cotações:** abra Minha Carteira, cadastre ou edite ticker, quantidade, preço médio, data e taxas. Teste atualizar e remover posições; os preços e o P&L chegam pelo WebSocket segundo o intervalo configurado.
3. **Dashboard:** confira indicadores, gráfico do Ibovespa (1D a 1A), composição, variações, sinais e Focus. Se a fonte não retornar histórico ou macroindicadores, a interface explica a indisponibilidade.
4. **Oportunidades e watchlist:** cadastre tickers, configure alertas e filtre quedas por dia/semana/mês, volume médio e setor. “Favorável” exige que todos os critérios fundamentalistas configurados estejam atendidos; informação ausente nunca é tratada como aprovação.
5. **Sinais e backtest:** critérios, pesos e limites são editáveis em `app/signals/rules.json`. Os sinais mostram resultados e valores usados. O backtest atual é **técnico**: fundamentos históricos completos não estão disponíveis de forma consistente na fonte e não são projetados para trás; a limitação é indicada no resultado.
6. **Relatórios:** gere um relatório na tela Relatórios ou configure horários em `REPORT_BEFORE_OPEN` e `REPORT_AFTER_CLOSE`. Configure SMTP (`SMTP_*`, `REPORT_EMAIL_TO`) e/ou Telegram (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) para envio. Os relatórios são salvos no SQLite mesmo quando o envio externo falha; erros de envio aparecem na resposta e nos logs.

## Testes

```powershell
python -m pytest -q
```

A suíte cobre hash de senha, calendário de pregões, cálculo de P&L com taxas, preço médio ponderado, critérios dos sinais e backtest. `GET /health` testa a disponibilidade da aplicação; páginas e APIs exigem sessão, exceto login e saúde.

## Estrutura

- `app/api/`: rotas FastAPI, login e APIs da aplicação.
- `app/services/`: autenticação, provedores de mercado, carteira, calendário e relatórios.
- `app/signals/`: motor auditável e `rules.json`.
- `app/models.py`, `app/database.py`: modelos SQLAlchemy e SQLite.
- `app/templates/`, `app/static/`: interface HTML/CSS/JavaScript.
- `config/b3_holidays.json`: exceções manuais do calendário da B3.
- `tests/`: testes do cálculo de P&L, autenticação, sinais e calendário.
