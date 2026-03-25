# BTG Pix — BILLmanager 6 Payment Module

Payment gateway module for [BILLmanager 6](https://www.ispsystem.com/billmanager) that integrates with [BTG Pactual Empresas](https://empresas.btgpactual.com/) to receive Pix payments directly inside the billing panel.

> **Production-ready** module with OAuth2 Authorization Code flow, QR code rendering, webhook support, and automatic payment polling.

---

## Features

- **Pix QR Code** — customers scan or copy-paste the Pix code to pay
- **Real-time webhook** — BTG notifies the system instantly when a payment is confirmed
- **Auto-polling** — periodic status checks ensure no payment is missed
- **Embedded OAuth2** — admin authorizes BTG access without leaving BILLmanager
- **Sandbox support** — toggle between production and sandbox environments
- **Token auto-refresh** — expired tokens are refreshed transparently and persisted to the database

---

## Tech Stack

| Category | Stack |
|----------|-------|
| Language | [Python 3](https://www.python.org/) |
| Platform | [BILLmanager 6](https://www.ispsystem.com/billmanager) (ISPsystem) |
| API | [BTG Pactual Empresas — Pix Cobranca](https://developers.empresas.btgpactual.com/) |
| Auth | OAuth2 Authorization Code + Refresh Token |
| Transport | CGI (BILLmanager standard) |

---

## Project Structure

```
billmanager-btgpix/
├── btgpix/                        # shared library package
│   ├── __init__.py                # package init + sys.path setup
│   ├── api.py                     # BTG Pactual HTTP client (OAuth2, collections)
│   ├── enums.py                   # CollectionStatus enum + status sets
│   ├── exceptions.py              # BTGApiError > BTGAuthError, BTGResponseError
│   └── utils.py                   # API builders, SQL lookups, HTML escaping
├── pmbtgpix.py                    # paymethod module (pm_validate, check_pay)
├── btgpixpayment.py               # payment CGI — QR code page for customers
├── btgpixwebhook.py               # webhook CGI — receives BTG events + polling
├── btgpixauth.py                  # OAuth2 CGI — embedded authorization flow
├── xml/
│   └── billmgr_mod_pmbtgpix.xml   # admin form definition (fields, labels, errors)
├── dist/
│   └── skins/common/plugin-logo/
│       └── billmanager-plugin-pmbtgpix.png  # Pix logo for the admin panel
├── Makefile                       # installation targets
└── README.md
```

---

## How It Works

### Payment Flow

1. Customer clicks **Pay** in BILLmanager
2. `btgpixpayment.py` creates a Pix collection on BTG and renders a page with the QR code
3. Customer scans the QR code or pastes the Pix code in their banking app
4. BTG sends an `instant-collections.paid` webhook to `btgpixwebhook.py`
5. The payment is marked as paid in BILLmanager automatically

### OAuth2 Authorization

1. Admin navigates to `/mancgi/btgpixauth?paymethod_id=<ID>`
2. Admin authenticates on BTG's login page and grants consent
3. BTG redirects back with an authorization code
4. The CGI exchanges the code for access + refresh tokens and saves them to the database

### Automatic Polling

`pmbtgpix.py` runs periodically via BILLmanager's `check_pay` mechanism. It queries all pending payments, checks their status on BTG, and updates them accordingly (paid, canceled, or expired after 3 days).

---

## Dependencies

```bash
# Debian-based
apt install -y make billmanager-corporate-dev billmanager-plugin-python-libs python3-venv

# RHEL-based
dnf install -y make billmanager-corporate-devel billmanager-plugin-python-libs
```

The `billmanager-plugin-python-libs` package provides the `billmgr` Python library. If needed, install fresh libs manually:

```bash
rm -rf /usr/local/mgr5/lib/python/billmgr && tar -xzvf billmgr.tar.gz -C /
```

---

## Installation

```bash
git clone https://github.com/Raggzinn/billmanager-btgpix.git
cd billmanager-btgpix
make install
```

This copies all scripts, the XML form, the library package, and the logo to the correct BILLmanager directories.

---

## Setup

1. In BILLmanager, go to **Provider** → **Payment Methods** → **Add**
2. Select **BTG Pactual Pix** from the module list
3. Fill in your credentials: **Client ID**, **Client Secret**, **Company ID**, **Pix Key**
4. Toggle **Sandbox** if testing
5. Save the payment method
6. Click the **Authorize with BTG** link to complete the OAuth2 flow
7. Register the webhook URL in BTG's developer portal: `https://<your-host>/mancgi/btgpixwebhook`

---

## BTG Pactual App Setup

1. Access the [BTG Empresas Developer Portal](https://developers.empresas.btgpactual.com/)
2. Create an app with model **First party — Consumir recursos de contas próprias**
3. Request the scope `openid empresas.btgpactual.com/pix-cash-in`
4. Set the redirect URI to `https://<your-host>/mancgi/btgpixauth`
5. Wait for BTG approval, then use the provided Client ID and Client Secret

---

## Contact

- [GitHub](https://github.com/Raggzinn/)
- [LinkedIn](https://www.linkedin.com/in/joaogoliveirac/)
- [Instagram](https://www.instagram.com/carrneiroo.j/)

---

---

# BTG Pix — Módulo de Pagamento BILLmanager 6

Módulo de gateway de pagamento para o [BILLmanager 6](https://www.ispsystem.com/billmanager) que integra com o [BTG Pactual Empresas](https://empresas.btgpactual.com/) para receber pagamentos via Pix diretamente no painel de cobrança.

> **Pronto para produção** com fluxo OAuth2 Authorization Code, renderização de QR code, suporte a webhook e polling automático de pagamentos.

---

## Funcionalidades

- **QR Code Pix** — cliente escaneia ou copia o código Pix para pagar
- **Webhook em tempo real** — o BTG notifica o sistema instantaneamente quando o pagamento é confirmado
- **Polling automático** — verificações periódicas garantem que nenhum pagamento seja perdido
- **OAuth2 embutido** — admin autoriza o acesso ao BTG sem sair do BILLmanager
- **Suporte a sandbox** — alterne entre ambientes de produção e sandbox
- **Auto-refresh de token** — tokens expirados são renovados automaticamente e persistidos no banco

---

## Stack

| Categoria | Stack |
|-----------|-------|
| Linguagem | [Python 3](https://www.python.org/) |
| Plataforma | [BILLmanager 6](https://www.ispsystem.com/billmanager) (ISPsystem) |
| API | [BTG Pactual Empresas — Pix Cobrança](https://developers.empresas.btgpactual.com/) |
| Autenticação | OAuth2 Authorization Code + Refresh Token |
| Transporte | CGI (padrão BILLmanager) |

---

## Estrutura do Projeto

```
billmanager-btgpix/
├── btgpix/                        # pacote de biblioteca compartilhada
│   ├── __init__.py                # init do pacote + configuração sys.path
│   ├── api.py                     # cliente HTTP BTG Pactual (OAuth2, cobranças)
│   ├── enums.py                   # enum CollectionStatus + conjuntos de status
│   ├── exceptions.py              # BTGApiError > BTGAuthError, BTGResponseError
│   └── utils.py                   # construtores de API, consultas SQL, escape HTML
├── pmbtgpix.py                    # módulo paymethod (pm_validate, check_pay)
├── btgpixpayment.py               # CGI de pagamento — página com QR code para o cliente
├── btgpixwebhook.py               # CGI webhook — recebe eventos BTG + polling
├── btgpixauth.py                  # CGI OAuth2 — fluxo de autorização embutido
├── xml/
│   └── billmgr_mod_pmbtgpix.xml   # definição do formulário admin (campos, labels, erros)
├── dist/
│   └── skins/common/plugin-logo/
│       └── billmanager-plugin-pmbtgpix.png  # logo do Pix no painel admin
├── Makefile                       # targets de instalação
└── README.md
```

---

## Como Funciona

### Fluxo de Pagamento

1. Cliente clica em **Pagar** no BILLmanager
2. `btgpixpayment.py` cria uma cobrança Pix no BTG e renderiza a página com o QR code
3. Cliente escaneia o QR code ou cola o código Pix no app do banco
4. BTG envia um webhook `instant-collections.paid` para `btgpixwebhook.py`
5. O pagamento é marcado como pago no BILLmanager automaticamente

### Autorização OAuth2

1. Admin navega para `/mancgi/btgpixauth?paymethod_id=<ID>`
2. Admin se autentica na página de login do BTG e concede permissão
3. BTG redireciona de volta com um código de autorização
4. O CGI troca o código por tokens de acesso + refresh e salva no banco de dados

### Polling Automático

`pmbtgpix.py` roda periodicamente via mecanismo `check_pay` do BILLmanager. Ele consulta todos os pagamentos pendentes, verifica o status no BTG e atualiza conforme necessário (pago, cancelado ou expirado após 3 dias).

---

## Dependências

```bash
# Debian-based
apt install -y make billmanager-corporate-dev billmanager-plugin-python-libs python3-venv

# RHEL-based
dnf install -y make billmanager-corporate-devel billmanager-plugin-python-libs
```

O pacote `billmanager-plugin-python-libs` fornece a biblioteca Python `billmgr`. Se necessário, instale as libs atualizadas manualmente:

```bash
rm -rf /usr/local/mgr5/lib/python/billmgr && tar -xzvf billmgr.tar.gz -C /
```

---

## Instalação

```bash
git clone https://github.com/Raggzinn/billmanager-btgpix.git
cd billmanager-btgpix
make install
```

Isso copia todos os scripts, o XML do formulário, o pacote da biblioteca e o logo para os diretórios corretos do BILLmanager.

---

## Configuração

1. No BILLmanager, vá em **Provedor** → **Métodos de Pagamento** → **Adicionar**
2. Selecione **BTG Pactual Pix** na lista de módulos
3. Preencha suas credenciais: **Client ID**, **Client Secret**, **Company ID**, **Chave Pix**
4. Ative **Sandbox** se estiver testando
5. Salve o método de pagamento
6. Clique no link **Autorizar com BTG** para completar o fluxo OAuth2
7. Registre a URL do webhook no portal de desenvolvedores do BTG: `https://<seu-host>/mancgi/btgpixwebhook`

---

## Configuração do App no BTG Pactual

1. Acesse o [Portal de Desenvolvedores BTG Empresas](https://developers.empresas.btgpactual.com/)
2. Crie um app com modelo **First party — Consumir recursos de contas próprias**
3. Solicite o escopo `openid empresas.btgpactual.com/pix-cash-in`
4. Defina a URI de redirecionamento como `https://<seu-host>/mancgi/btgpixauth`
5. Aguarde a aprovação do BTG, depois use o Client ID e Client Secret fornecidos

---

## Contato

- [GitHub](https://github.com/Raggzinn/)
- [LinkedIn](https://www.linkedin.com/in/joaogoliveirac/)
- [Instagram](https://www.instagram.com/carrneiroo.j/)
