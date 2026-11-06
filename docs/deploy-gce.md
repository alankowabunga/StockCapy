# 部署到 GCE（always-free e2-micro）

目標：讓手機隨時隨地看得到資料、並讓排程自己跑。設計成落在 GCP always-free 額度內，**月費 $0**。

> 免費額度與定價會變動，本文寫於 2026-09。動手前請先在 console 確認當下條款。

## 下文使用的佔位符

| 佔位符 | 意思 |
|---|---|
| `<PROJECT_ID>` | 你要建立的 GCP 專案 ID（全域唯一） |
| `<BILLING_ACCOUNT_ID>` | 帳單帳戶 ID（`gcloud billing accounts list` 可查） |
| `<YOUR_EMAIL>` | 建專案、開 VM 用的 Google 帳號 |
| `<BILLING_ADMIN_EMAIL>` | 帳單帳戶的管理者（若與上面是同一人可略過授權步驟） |
| `<VM_IP>` | VM 的外部靜態 IP |
| `<your-github-user>/<repo>` | 你 fork 或 clone 的 GitHub repo |

> 只有 `roles/billing.user` 的帳號能把專案掛上帳單，但**看不到費用**；查費用要用有
> `roles/billing.admin` 或 `billing.viewer` 的帳號登入 console 的帳單頁面。

## always-free 的三個硬條件

全部滿足才免費，缺一個就開始計費：

1. **機型只能 `e2-micro`**（不是 e2-small、不是 e2-medium）
2. **區域只能 `us-west1` / `us-central1` / `us-east1`**，每月 1 台
3. **磁碟只能 `pd-standard`，總計 30 GB**

因此：**不要另外掛第二顆資料磁碟**。開機磁碟本身就是持久的，30 GB 放一個 216 KB 的資料庫綽綽有餘；加第二顆會超出免費額度。

同一個帳單帳戶下若已有其他機型或區域的 VM（例如 asia 區的 e2-medium），它們**不佔用**這份額度，但也不會變免費。

## 0. 先建一個獨立的 gcloud configuration

**不要直接跑 `gcloud config set account`。** 那會改動當前 active 的 configuration。如果你的
`default` 是別的帳號（例如工作帳號），且預設 zone 不在免費區域（例如 `asia-east1-b`），改它有兩個後果：

1. 你在其他終端跑不相關的 gcloud 指令會變成這個帳號
2. **預設 zone 若留在免費區域以外，任何忘記加 `--zone` 的 compute 指令都會把資源開在那裡
   —— 那不在 always-free 區域，直接開始計費**

所以開一個專屬 configuration：

```bash
gcloud config configurations create stockcapy
gcloud config set account <YOUR_EMAIL>
gcloud config set compute/region us-central1
gcloud config set compute/zone us-central1-a
```

`configurations create` 會自動切過去，之後的 `gcloud config set` 只影響 `stockcapy` 這一份。
要回到原本的環境：

```bash
gcloud config configurations activate default
```

隨時確認自己在哪一份：`gcloud config configurations list`

## 1. 建專案並連帳單

```bash
gcloud projects create <PROJECT_ID> --name="StockCapy"
gcloud billing projects link <PROJECT_ID> --billing-account=<BILLING_ACCOUNT_ID>
gcloud config set project <PROJECT_ID>
gcloud services enable compute.googleapis.com
```

> **踩過的坑**：`gcloud billing accounts *` 這組指令的位置參數叫 `account`，會蓋掉全域的
> `--account` 認證旗標，導致 `INVALID_ARGUMENT`。若帳單管理者是另一個帳號，授權那一步（由管理者執行）
> 要用環境變數指定身分，而且**不要**依賴 configuration —— 那一步的身分跟後續步驟不同：
>
> ```
> CLOUDSDK_CORE_ACCOUNT=<BILLING_ADMIN_EMAIL> gcloud billing accounts add-iam-policy-binding \
>   <BILLING_ACCOUNT_ID> --member="user:<YOUR_EMAIL>" --role="roles/billing.user"
> ```
>
> （`gcloud billing projects link` 沒這個問題，它的位置參數是專案 ID，用 configuration 即可。）

## 2. 開 VM

```bash
gcloud compute instances create stockcapy \
  --zone=us-central1-a \
  --machine-type=e2-micro \
  --boot-disk-type=pd-standard \
  --boot-disk-size=30GB \
  --image-family=debian-12 \
  --image-project=debian-cloud
```

**先不開額外的防火牆規則**——對外只會開 80/443 給 Caddy（第 4 步），8765 不對外。

新專案的 default VPC 會自帶 `default-allow-ssh`（22）與 `default-allow-rdp`（3389），
兩者都對 `0.0.0.0/0`。RDP 在 Linux 上用不到，刪掉減少攻擊面：

```bash
gcloud compute firewall-rules delete default-allow-rdp --project=<PROJECT_ID> --quiet
```

SSH 保留（`gcloud compute ssh` 要用），它是金鑰認證、不接受密碼。

## 3. 網路：Caddy + 免費 wildcard DNS，取得真正的 HTTPS

這台機器上有你的持倉、成本、帳戶淨值。**不要直接把 8765 開到公網**——那是純 HTTP，
API token 會以明文傳輸，攔到就等於拿到完整讀寫權；而且公網的 8765 幾小時內就會被掃描器找到。

做法：Caddy 當反向代理、app 只綁 `127.0.0.1`、用免費 wildcard DNS 拿到可簽憑證的網域名稱。
**不需要買網域、不需要註冊任何帳號、手機也不用裝任何 app。**

### 3.1 靜態 IP

DNS 名稱綁在 IP 上，所以 IP 不能變（ephemeral IP 在 VM 停機後會換掉）：

```bash
gcloud compute addresses create stockcapy-ip --project=<PROJECT_ID> \
  --region=us-central1 --addresses=<VM 目前的外部 IP>
```

計費與 ephemeral 相同，只是保證不變。

### 3.2 開 80/443

```bash
gcloud compute firewall-rules create allow-http-https --project=<PROJECT_ID> \
  --allow=tcp:80,tcp:443 --source-ranges=0.0.0.0/0
```

80 是 ACME HTTP-01 驗證用，443 是服務本身。**8765 始終不開**。

### 3.3 app 改綁 localhost

```bash
sudo sed -i 's|--host 0.0.0.0|--host 127.0.0.1|' /etc/systemd/system/stockcapy.service
sudo systemctl daemon-reload && sudo systemctl restart stockcapy
```

這樣 8765 只有 Caddy 連得到，就算防火牆哪天被誤開也沒有暴露面。

### 3.4 Caddy

`sslip.io` 這類免費 wildcard DNS 會把 `<VM_IP>.sslip.io` 自動解析到 `<VM_IP>`（例如 `203.0.113.10.sslip.io` → `203.0.113.10`）——
無需註冊，而且是真實公開 DNS 名稱，Let's Encrypt 可以直接簽發。

```bash
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update && sudo apt-get install -y caddy

sudo tee /etc/caddy/Caddyfile > /dev/null <<'CADDY'
<VM_IP>.sslip.io {
	reverse_proxy 127.0.0.1:8765
}
CADDY
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl restart caddy
```

Caddy 會自動申請並續期憑證。實測第一次請求就簽發成功。

### 3.5 驗證（從 VM 外面測，這才是真正重要的）

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://<VM_IP>.sslip.io/          # 200
curl -s https://<VM_IP>.sslip.io/api/session                                  # authentication_required: true
curl -s -o /dev/null -w "%{http_code}\n" https://<VM_IP>.sslip.io/api/targets # 401（無 token）
curl -s -m 8 -o /dev/null -w "%{http_code}\n" http://<VM_IP>:8765/            # 000（正確擋掉）
echo | openssl s_client -connect <VM_IP>.sslip.io:443 2>/dev/null | openssl x509 -noout -issuer -dates
```

手機與 app 的 base URL 就填 `https://<VM_IP>.sslip.io`。

> **`sslip.io` 的限制**：它不在 Public Suffix List 上，所以 Let's Encrypt 的速率限制
> （每個註冊網域每週 50 張）是所有 sslip.io 使用者共用的。實測順利簽發，但若哪天
> 遇到簽發失敗，改用 DuckDNS（免費子網域、需註冊帳號）換掉 Caddyfile 裡的網域即可。

> **為什麼不用 Tailscale**：私有網段的安全性更好（零公網暴露），但 `tailscale up` 在
> 非互動終端不會印出授權連結（`status --json` 的 `AuthURL` 是空的、`BackendState`
> 停在 `NeedsLogin`），而且手機必須常駐 Tailscale app。上面這條路手機零安裝。

## 4. 裝服務

若 repo 是私有的（例如你 fork 後改成 private），需要拉取權限。用**唯讀 deploy key**，不要把個人 SSH key 放上 VM；公開 repo 可略過 deploy key、直接用 HTTPS clone：

```bash
# VM 上
ssh-keygen -t ed25519 -f ~/.ssh/stockcapy_deploy -N ""
cat ~/.ssh/stockcapy_deploy.pub
```

把那串公鑰貼到 GitHub → repo → Settings → Deploy keys → Add（**不要**勾 Allow write access）。

> **踩過的坑：Debian 12 內建 Python 3.11，但 `pyproject.toml` 要求 `>=3.12`。**
> 不要改 `requires-python` 去遷就系統 Python（會踩到未知的相容性問題）；用 uv 裝一份
> 獨立的 3.12，跟本機開發環境保持一致。
>
> **踩過的坑：1 GB RAM 裝 pandas/numpy 會偏緊。** 先加 1 GB swap 當保險：
>
> ```
> sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
> echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab
> ```

```bash
# VM 上
sudo apt-get update && sudo apt-get install -y python3-venv git
cat >> ~/.ssh/config <<'SSH'
Host github.com
  IdentityFile ~/.ssh/stockcapy_deploy
SSH
git clone --branch main git@github.com:<your-github-user>/<repo>.git ~/stockcapy

# uv 會下載獨立的 CPython 3.12，不動系統 Python
curl -fsSL https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
cd ~/stockcapy && uv venv --python 3.12 && uv pip install -e .
```

拉 `main`（發版分支），不是 `develop` —— 見 README 的分支策略。

## 5. 設定與資料

`.env` **不在版控裡**，要手動建立並鎖權限：

```bash
# VM 上
cd ~/stockcapy
.venv/bin/stockcapy token --write-env      # 產生 API token
cat >> .env <<'ENV'
PORTFOLIO_TELEGRAM_BOT_TOKEN=你的token
PORTFOLIO_TELEGRAM_CHAT_ID=你的chat_id
PORTFOLIO_DESKTOP_NOTIFICATIONS=0
PORTFOLIO_CHECK_INTERVAL_MINUTES=30
ENV
chmod 600 .env
```

`PORTFOLIO_DESKTOP_NOTIFICATIONS=0` 是必要的——Linux 上沒有 `osascript`，不關掉每次都會回報失敗。
`PORTFOLIO_CHECK_INTERVAL_MINUTES=30` 會啟動 app 內建排程，**不需要 cron 或 systemd timer**。

把本機的資料庫搬上去（216 KB）：

```bash
# 本機
gcloud compute scp data/portfolio.sqlite3 stockcapy:~/stockcapy/data/ --zone=us-central1-a
```

## 6. systemd 開機自啟

```bash
# VM 上。$USER 與 $HOME 由 shell 在寫入時展開，unit 檔裡留下的是實際路徑。
sudo tee /etc/systemd/system/stockcapy.service > /dev/null <<UNIT
[Unit]
Description=StockCapy
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$HOME/stockcapy
EnvironmentFile=$HOME/stockcapy/.env
ExecStart=$HOME/stockcapy/.venv/bin/stockcapy serve --host 0.0.0.0 --port 8765
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload && sudo systemctl enable --now stockcapy
systemctl status stockcapy --no-pager
```

> 注意上面的 heredoc 是 `<<UNIT` 而**不是** `<<'UNIT'`——要讓 shell 展開 `$USER` 與 `$HOME`。
> 加了 `EnvironmentFile` 是因為 systemd 不會經過你的 shell，不讀 `.env`；不指定的話
> token 與 Telegram 設定都不會生效，服務會因 fail-closed 拒絕啟動。

綁 `0.0.0.0` 需要 `.env` 裡有 `PORTFOLIO_API_TOKEN`，否則 `serve` 會**拒絕啟動**（fail-closed，見 decisions.md D7）。這是刻意的：8765 不對公網開放，對外只經 Caddy 的 HTTPS，token 是存取 API 的唯一門檻。

驗證通知管道：

```bash
cd ~/stockcapy && .venv/bin/stockcapy notify-test
```

## 7. 備份

資料庫只有 216 KB，一個檔案的事。用 SQLite 的 `.backup` 才安全（直接 cp 可能抓到寫入中的狀態）：

```bash
# VM 上，先建目錄
mkdir -p ~/stockcapy/backups

# 再寫成腳本（放進 crontab 的單行版本容易被引號咬到）
cat > ~/stockcapy/backups/snapshot.py <<'PY'
import sqlite3, datetime, pathlib
root = pathlib.Path(__file__).resolve().parents[1]
target = root / "backups" / f"portfolio-{datetime.date.today()}.sqlite3"
source = sqlite3.connect(root / "data" / "portfolio.sqlite3")
copy = sqlite3.connect(target)
source.backup(copy)          # 線上備份，不會抓到寫入中的狀態
copy.close(); source.close()
print(target)
PY

# crontab -e，每天台灣時間下午 4 點（VM 是 UTC，08:00 UTC = 16:00 台北）
0 8 * * * /home/USER/stockcapy/.venv/bin/python /home/USER/stockcapy/backups/snapshot.py
```

拉回本機：

```bash
gcloud compute scp stockcapy:~/stockcapy/backups/portfolio-*.sqlite3 ./backups/ --zone=us-central1-a
```

（要更省事可以推到 Cloud Storage，us 區域有 5 GB 免費額度。）

## 8. 更新部署

```bash
gcloud compute ssh stockcapy --configuration=stockcapy --zone=us-central1-a --command \
  'cd ~/stockcapy && git pull && VIRTUAL_ENV=$HOME/stockcapy/.venv ~/.local/bin/uv pip install -e . -q && sudo systemctl restart stockcapy'
```

依分支策略，VM 上應該拉 `main`（發版分支），不是 `develop`。

VM 上的 venv 是 `uv` 建的，**沒有 `pip`**，所以要用 `uv pip`；`uv` 不在預設 PATH，
非互動式 ssh 取不到，得寫絕對路徑。`--configuration=stockcapy` 同理不能省——
若你機器上 active 的是別的 configuration（例如工作帳號、zone 不在免費區域），省略就會打錯專案。

## 費用檢查清單

上線後第一週去確認一次，避免掉出免費額度：

- [ ] **自己在 `stockcapy` 這份 configuration 裡**（`gcloud config configurations list`）
- [ ] 機型是 `e2-micro`（`gcloud compute instances list`）
- [ ] 區域在 us-west1 / us-central1 / us-east1
- [ ] 磁碟是 `pd-standard` 且 ≤ 30 GB（`gcloud compute disks list`）
- [ ] 每月 egress < 1 GB（單人用不會接近）
- [ ] **外部 IPv4 是否計費** —— 官方 always-free 文件只明列「e2-micro / 30GB pd-standard /
      1GB 北美 egress」三項，**沒有寫外部 IP**。GCP 自 2024-02 起對外部 IPv4 收費，
      但免費額度的 e2-micro 是否豁免，官方文件沒有明確敘述。第一次帳單出來時確認這一項；
      若計費約 $3/月。（不能單純移除 IP —— VM 需要對外網路抓行情，拿掉就要 Cloud NAT，那更貴。）
- [ ] 帳單頁面確認 StockCapy 專案是 $0（要用有帳單檢視權限的帳號登入）

## 為什麼不用 Cloud Run / GKE

- **Cloud Run**：無狀態，持久儲存只能掛 GCS FUSE 或 NFS，而 **SQLite 依賴 POSIX 檔案鎖**，這兩種掛載都無法正確提供 → 有資料庫損毀風險。裡面是你的持倉紀錄，不值得賭。
- **GKE**：叢集管理費約 $0.10/hr（≈$73/月，有一個 zonal cluster 可被免費額度折抵），**再加 node VM 費用**；always-free 的 e2-micro 不適用於 GKE node。為一個 216 KB 的單人服務開叢集，成本與複雜度都不成比例。
