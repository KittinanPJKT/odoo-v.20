# คู่มือติดตั้งและบริหารจัดการ Odoo 20 แบบ 3 Environments (Server จริง)

ชุดไฟล์ในโฟลเดอร์ `deploy/` ถูกออกแบบมาเพื่อรัน Odoo 20 แยกเป็น **3 ส่วนอิสระบนเครื่อง Linux Server 1 เครื่อง (4 Cores / 8 Threads)** พร้อม Reverse Proxy (**Caddy HTTPS อัตโนมัติ**) และระบบ **Backup/Restore + Cloud Sync (`rclone`)**

---

## 1. โครงสร้างทั้ง 3 Environments

| Environment | ชื่อเรียกระบบ | Database Name | Container Odoo / DB | Workers / Mode | DB Manager (`/web/database`) | Outbound Email |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **1. `test-a`** | ทีมทดลอง/ทดสอบระบบ | `odoo_test_a` | `odoo-test-a` / `db-test-a` | `workers=0` (Threaded) | เปิด (`^odoo_test_a`) | ดักลง **Mailpit** |
| **2. `test-b`** | Demo / UAT ก่อนขึ้นจริง | `odoo_test_b` | `odoo-test-b` / `db-test-b` | `workers=2`, `gevent=1` | ปิด (บล็อกที่ Caddy + `list_db=False`) | ปิด + ดักลง **Mailpit** |
| **3. `prod`** | **ตัวจริง (ลูกค้าใช้งานจริง)** | `odoo_prod` | `odoo-prod` / `db-prod` | `workers=4`, `gevent=1` | ปิดถาวร (บล็อกที่ Caddy + `list_db=False`) | **เปิดใช้ SMTP จริง** |

> **ข้อดีของสถาปัตยกรรมนี้:**
> * แต่ละ Environment แยก **PostgreSQL Container, Network (`internal: true`), และ Docker Volume** ขาดจากกัน 100%
> * **Caddy Reverse Proxy** ออกและต่ออายุใบรับรอง HTTPS (Let's Encrypt) ให้อัตโนมัติ และส่ง `/websocket*` ไป `gevent_port 8072` ให้ถูกโหมด
> * รหัสผ่านทั้งหมดอยู่ในไฟล์ `deploy/env/*.env` ซึ่งถูกกันออกจาก Git ด้วย `.gitignore` แล้ว

---

## 2. ขั้นตอนเตรียมเครื่อง Server จริง (ทำครั้งแรกครั้งเดียว)

### 2.1 เตรียม Ubuntu Server & Docker
```bash
# ตั้ง Timezone เป็นเวลาไทย (เพื่อให้ Cron Backup และ Log ตรงเวลาจริง 02:00 น.)
sudo timedatectl set-timezone Asia/Bangkok

# อัปเดตระบบและติดตั้งเครื่องมือพื้นฐาน
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl openssl rclone

# ติดตั้ง Docker Engine + Docker Compose Plugin (Official script)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
# (Logout แล้ว Login ใหม่เพื่อให้สิทธิ์ docker มีผล)
```

### 2.2 ตั้งค่า Firewall (UFW) & Hardening Linux Host
เปิดเฉพาะ SSH (22), HTTP (80), และ HTTPS (443) **ห้ามเปิดพอร์ต PostgreSQL (5432/5434) หรือ Mailpit (8025/8026) ออกสาธารณะเด็ดขาด**
```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 443/udp
sudo ufw enable

# ป้องกันบอทยิงเดารหัส SSH และเปิดอัปเดตแพตช์ความปลอดภัยอัตโนมัติ
sudo apt install -y fail2ban unattended-upgrades
sudo systemctl enable --now fail2ban
sudo dpkg-reconfigure -plow unattended-upgrades
```
> **แนะนำเพิ่มเติมสำหรับ SSH:** ใน `/etc/ssh/sshd_config` ให้ตั้งค่า `PermitRootLogin no` และใช้ SSH Key แทนรหัสผ่าน (`PasswordAuthentication no`) แล้วรัน `sudo systemctl restart ssh` รวมถึงจำกัดสิทธิ์กลุ่ม `docker` เฉพาะผู้ดูแลระบบตัวจริงเท่านั้น

### 2.3 ชี้ DNS Domain มาที่ IP ของ Server
ตั้งค่า DNS A Record ทั้ง 3 ชื่อให้ชี้มาที่ Public IP ของเครื่องเซิร์ฟเวอร์จริง:
* `erp.yourdomain.com` ➔ `<SERVER_IP>` (`prod` - ตัวจริง)
* `demo.yourdomain.com` ➔ `<SERVER_IP>` (`test-b` - Demo)
* `dev.yourdomain.com` ➔ `<SERVER_IP>` (`test-a` - ทีมทดสอบ)

---

## 3. ขั้นตอนติดตั้งและสั่งรันระบบ

### 3.1 Clone โปรเจกต์ และแตกโฟลเดอร์โค้ด 3 ส่วน (แนะนำใช้ `git worktree`)
การแยกโฟลเดอร์ `custom_addons` ของแต่ละ Environment ช่วยป้องกันไม่ให้โค้ดที่กำลังแก้ใน `test-a` ไปทำให้ `prod` พัง:

```bash
sudo mkdir -p /opt/odoo-deploy && sudo chown "$USER:$USER" /opt/odoo-deploy
cd /opt/odoo-deploy

# 1. Clone repo หลักสำหรับรัน deploy scripts และ production
git clone -b odoo-etnc-construction https://github.com/KittinanPJKT/odoo-v.20.git main-repo

# 2. (แนะนำ) แยก Worktree สำหรับ test-b (staging/demo) และ test-a (dev)
cd /opt/odoo-deploy/main-repo
git worktree add /opt/odoo-deploy/worktree-test-b odoo-etnc-construction
git worktree add -b feat/dev-test /opt/odoo-deploy/worktree-test-a odoo-etnc-construction
```

### 3.2 สร้างไฟล์ `.env` และรหัสผ่านสุ่มอัตโนมัติ
```bash
cd /opt/odoo-deploy/main-repo
./deploy/scripts/init-server.sh
```

จากนั้นเข้าไปแก้ **ชื่อ Domain** และ **Path ของ Worktree** ในไฟล์ต่อไปนี้:
1. `deploy/env/proxy.env` ➔ ใส่ `ACME_EMAIL`, `PROD_DOMAIN`, `TEST_B_DOMAIN`, `TEST_A_DOMAIN`
2. `deploy/env/prod.env` ➔ แก้ `ODOO_BASE_URL=https://erp.yourdomain.com` (และ `CUSTOM_ADDONS_PATH=../custom_addons` หรือ `/opt/odoo-deploy/main-repo/custom_addons`)
3. `deploy/env/test-b.env` ➔ แก้ `ODOO_BASE_URL=https://demo.yourdomain.com` และ `CUSTOM_ADDONS_PATH=/opt/odoo-deploy/worktree-test-b/custom_addons`
4. `deploy/env/test-a.env` ➔ แก้ `ODOO_BASE_URL=https://dev.yourdomain.com` และ `CUSTOM_ADDONS_PATH=/opt/odoo-deploy/worktree-test-a/custom_addons`

### 3.3 สั่ง Build และเปิดใช้งานทั้งหมด
```bash
./deploy/scripts/deploy.sh all up
```
ตรวจสถานะความเรียบร้อย:
```bash
./deploy/scripts/deploy.sh all status
```
> **(แนะนำ) ล็อกหน้าเว็บ `test-a` / `test-b` ด้วยรหัสผ่านชั้นหน้า Proxy:** หลังจากรัน Proxy แล้วสามารถสร้าง hash ด้วย `docker exec odoo-caddy caddy hash-password` แล้วนำไป uncomment บล็อก `basic_auth` ใน `deploy/proxy/Caddyfile` เพื่อกันคนนอกเปิดเข้าหน้า `dev`/`demo` (แล้วรัน `./deploy/scripts/deploy.sh proxy restart`)

---

## 4. การสร้างหรือย้าย Database ทั้ง 3 ตัว (`ตัวจริง` / `ทดสอบ A` / `ทดสอบ B`)

### กรณี A: ย้ายฐานข้อมูล `odoo-v20` จากเครื่อง Windows Dev ปัจจุบันขึ้น Server จริง
1. **บนเครื่อง Windows ปัจจุบัน** (เปิด Git Bash ในโฟลเดอร์โปรเจกต์ แล้ว Export ออกมาเป็นไฟล์เดียว):
   ```bash
   export MSYS_NO_PATHCONV=1
   mkdir -p tmp_migrate
   docker compose exec -T db pg_dump -U odoo -Fc --no-owner --no-acl odoo-v20 > tmp_migrate/db.dump
   docker run --rm -v odoo-v20_odoo-data:/var/lib/odoo:ro alpine:3.20 tar -C /var/lib/odoo/filestore -czf - odoo-v20 > tmp_migrate/filestore.tar.gz
   printf "database=odoo-v20\n" > tmp_migrate/metadata.txt
   tar -C tmp_migrate -czf odoo_migrate.tar.gz db.dump filestore.tar.gz metadata.txt
   rm -rf tmp_migrate
   ```
2. ส่งไฟล์ `odoo_migrate.tar.gz` ขึ้น Server (`scp odoo_migrate.tar.gz user@server:/tmp/`)
3. **บน Server จริง** สั่ง Restore ลงทั้ง 3 ระบบได้ทันที:
   ```bash
   ./deploy/scripts/restore.sh prod /tmp/odoo_migrate.tar.gz
   ./deploy/scripts/restore.sh test-b /tmp/odoo_migrate.tar.gz
   ./deploy/scripts/restore.sh test-a /tmp/odoo_migrate.tar.gz
   ```
   *(สคริปต์จะตั้งค่า `web.base.url`, `report.url=http://127.0.0.1:8069` สำหรับพิมพ์ PDF และปิด Mail Server ให้ `test-b`/`test-a` อัตโนมัติ)*

### กรณี B: สร้างฐานข้อมูลใหม่บน `test-a` แล้วติดตั้งโมดูล Custom
```bash
./deploy/scripts/deploy.sh test-a install base,web,account,l10n_th,thai_billing_reports,custom_invoice_report,web_home_menu
./deploy/scripts/deploy.sh test-a fix-urls
```

---

## 5. ระบบ Backup อัตโนมัติ & Cloud Off-site (`rclone`)

### 5.1 เชื่อมต่อ Cloud Storage แบบเข้ารหัสด้วย `rclone` + `rclone crypt` (ทำครั้งเดียว)
```bash
rclone config
# ขั้นที่ 1: สร้าง Remote ปลายทาง เช่น "gdrive" (Google Drive) หรือ "r2" (Cloudflare R2 / S3 / Backblaze B2)
# ขั้นที่ 2: สร้าง Remote แบบ Type = "crypt" ชื่อ "gdrive-crypt" ชี้ไปที่ "gdrive:entronica-odoo-backups-encrypted"
#          (ไฟล์ฐานข้อมูลจะถูกเข้ารหัสจากฝั่งเซิร์ฟเวอร์ก่อนอัปโหลดขึ้น Cloud เสมอ)
```
จากนั้นเปิดไฟล์ `deploy/env/prod.env` แล้วระบุชื่อ Crypt Remote:
```env
RCLONE_REMOTE=gdrive-crypt:
RCLONE_CLOUD_RETENTION_DAYS=30
```

### 5.2 ทดสอบรัน Backup และ Restore Drill ด้วยตนเอง
```bash
# ทดสอบ Backup ตัวจริง (จะได้ไฟล์ .tar.gz + .sha256 ใน deploy/backups/prod/daily/)
./deploy/scripts/backup.sh prod

# ทดสอบซ้อมกู้คืนจากไฟล์ Backup ตัวจริง ลงไปที่ Demo (test-b)
./deploy/scripts/restore.sh test-b deploy/backups/prod/daily/<ไฟล์ล่าสุด>.tar.gz
```

### 5.3 ตั้ง Cronjob ให้ Backup อัตโนมัติทุกคืน
```bash
crontab -e
```
เพิ่ม 2 บรรทัดนี้ (Backup ตัวจริงทุกวันเวลา **02:00 น.** และ Backup Demo ทุกวันอาทิตย์เวลา **03:00 น.**):
```cron
0 2 * * * /opt/odoo-deploy/main-repo/deploy/scripts/backup.sh prod >> /opt/odoo-deploy/main-repo/deploy/backups/backup-prod.log 2>&1
0 3 * * 0 /opt/odoo-deploy/main-repo/deploy/scripts/backup.sh test-b >> /opt/odoo-deploy/main-repo/deploy/backups/backup-demo.log 2>&1
```

---

## 6. คำสั่งที่ใช้บ่อยในชีวิตประจำวัน

| งานที่ต้องการทำ | คำสั่ง |
| :--- | :--- |
| อัปเดตโค้ด + โมดูลบน `test-a` | `cd /opt/odoo-deploy/worktree-test-a && git pull` <br> `cd /opt/odoo-deploy/main-repo && ./deploy/scripts/deploy.sh test-a update thai_billing_reports` |
| อัปเดตโค้ด + โมดูลบน `prod` *(หลังผ่าน Demo แล้ว)* | `cd /opt/odoo-deploy/main-repo && git pull` <br> `./deploy/scripts/backup.sh prod` *(Backup ก่อนอัปเดตเสมอ!)* <br> `./deploy/scripts/deploy.sh prod update custom_invoice_report` |
| ดู Log การทำงานของระบบจริง | `./deploy/scripts/deploy.sh prod logs` |
| เปิดดูอีเมลที่โดนดักไว้ใน `test-b` / `test-a` | ต่อ SSH Tunnel จากเครื่องตัวเอง: `ssh -L 8026:127.0.0.1:8026 user@server` แล้วเปิด `http://localhost:8026` |
| เข้าฐานข้อมูล `test-a` ผ่าน pgAdmin | ต่อ SSH Tunnel: `ssh -L 5434:127.0.0.1:5434 user@server` แล้วต่อ pgAdmin ไปที่ `localhost:5434` |

---

## 7. Checklist ก่อนเปิดให้ลูกค้าใช้งานจริง (Go-Live)
- [ ] DNS ชี้ตรงครบทั้ง 3 โดเมน และเปิด `https://erp.yourdomain.com` ขึ้นกุญแจ SSL ปกติ (พร้อม Security Headers / HSTS จาก Caddy)
- [ ] เข้า `https://erp.yourdomain.com/web/database/manager` แล้วต้องขึ้น **403 Forbidden** (โดนบล็อกเรียบร้อย)
- [ ] เปิดใช้งาน **Two-Factor Authentication (2FA)** บนบัญชี Administrator และผู้ใช้ระดับบัญชีทุกคนบน `prod`
- [ ] ล็อก `test-a` (และ `test-b` หากไม่เปิดสาธารณะ) ด้วย `basic_auth` หรือ Office/VPN IP ใน `deploy/proxy/Caddyfile` เรียบร้อย
- [ ] ทดสอบพิมพ์รายงาน PDF `Original Invoice (Entronica)` บน `prod` ต้องแสดงฟอนต์ไทย Sarabun, ตาราง, และโลโก้ครบสมบูรณ์ (ตรวจยืนยัน `report.url = http://127.0.0.1:8069`)
- [ ] ทดสอบส่งอีเมลบน `prod` สำเร็จ และทดสอบบน `test-b` ต้อง **ไม่ส่งออกถึงลูกค้าจริง** (ถูกดักลง Mailpit เท่านั้น)
- [ ] รัน `backup.sh prod` สำเร็จ มีไฟล์ขึ้น Cloud (`rclone ls gdrive-crypt:`) และซ้อม `restore.sh test-b` ผ่านเรียบร้อย

