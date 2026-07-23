# Deployment na Hostinger VPS

Upute za postavljanje aplikacije **Pjesme Print** (PDF pjesmarica → Word) na
Hostinger VPS. Nakon postavljanja aplikacija je dostupna kroz web preglednik:
uploadaš PDF, dobiješ `.docx`.

Dovoljan je i najmanji Hostinger plan (KVM 1). Preporučeni OS: **Ubuntu 24.04**
(ili 22.04).

---

## 0. Spajanje na VPS

1. U Hostinger hPanelu otvori **VPS → svoj server** i pronađi IP adresu te
   root lozinku (ili postavi SSH ključ pod *Settings → SSH keys*).
2. Spoji se sa svog računala:

   ```bash
   ssh root@IP-ADRESA
   ```

---

## Opcija A — Docker (preporučeno, najjednostavnije)

> Savjet: kod kreiranja VPS-a u Hostingeru možeš odabrati template
> **"Ubuntu 24.04 with Docker"** — tada preskoči korak 1.

### 1. Instaliraj Docker (ako već nije)

```bash
curl -fsSL https://get.docker.com | sh
```

### 2. Preuzmi aplikaciju

```bash
cd /opt
git clone https://github.com/pupsyyy/pjesme_print.git
cd pjesme_print
```

> Ako je repozitorij privatan, koristi GitHub *personal access token*:
> `git clone https://TOKEN@github.com/pupsyyy/pjesme_print.git`

### 3. Pokreni

```bash
docker compose up -d --build
```

To je to — aplikacija je dostupna na **http://IP-ADRESA:8010/**.

> Aplikacija namjerno koristi port **8010**, a ne 80 — tako port 80 ostaje
> slobodan za zajednički portal ili druge aplikacije na istom serveru
> (vidi [Više aplikacija na istom serveru](#više-aplikacija-na-istom-serveru)).
> Ako je 8010 zauzet, promijeni lijevi broj u `ports` u `docker-compose.yml`
> (npr. `"8020:8000"`).

### Korisne naredbe

```bash
docker compose logs -f          # logovi uživo
docker compose restart          # restart
docker compose down             # zaustavi
git pull && docker compose up -d --build   # ažuriranje na novu verziju
```

---

## Opcija B — bez Dockera (venv + systemd + nginx)

### 1. Instaliraj pakete

```bash
apt update
apt install -y python3-venv python3-pip git nginx
```

### 2. Preuzmi aplikaciju i pripremi okruženje

```bash
adduser --system --group --home /opt/pjesme_print pjesme
cd /opt
git clone https://github.com/pupsyyy/pjesme_print.git
cd pjesme_print
python3 -m venv venv
venv/bin/pip install -r requirements.txt
chown -R pjesme:pjesme /opt/pjesme_print
```

### 3. Systemd servis (automatsko pokretanje)

```bash
cp deploy/pjesme.service /etc/systemd/system/pjesme.service
systemctl daemon-reload
systemctl enable --now pjesme
systemctl status pjesme        # provjera — mora pisati "active (running)"
```

### 4. Nginx reverse proxy

```bash
cp deploy/nginx-pjesme.conf /etc/nginx/sites-available/pjesme
# Uredi server_name ako imaš domenu:
nano /etc/nginx/sites-available/pjesme
ln -s /etc/nginx/sites-available/pjesme /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
```

Aplikacija je sada na **http://IP-ADRESA/**.

### 5. Ažuriranje na novu verziju

```bash
cd /opt/pjesme_print
git pull
venv/bin/pip install -r requirements.txt
systemctl restart pjesme
```

---

## Domena i HTTPS (opcionalno, ali preporučeno)

1. U DNS postavkama domene dodaj **A zapis** koji pokazuje na IP VPS-a
   (npr. `pjesme.mojadomena.com → IP-ADRESA`). Ako je domena kod Hostingera,
   to radiš u hPanelu pod *Domains → DNS*.
2. Instaliraj besplatni Let's Encrypt certifikat:

   **Opcija B (nginx na hostu):**
   ```bash
   apt install -y certbot python3-certbot-nginx
   certbot --nginx -d pjesme.mojadomena.com
   ```
   Certbot sam uređuje nginx konfiguraciju i obnavlja certifikat.

   **Opcija A (Docker):** u `docker-compose.yml` zamijeni `"8010:8000"` s
   `"127.0.0.1:8010:8000"`, zatim instaliraj nginx na host
   (`apt install -y nginx`), postavi ga po koracima B4 i pokreni certbot kao
   gore.

---

## Više aplikacija na istom serveru

Ako na VPS-u već rade druge aplikacije, vrijede dva pravila:

1. **Svaka aplikacija na svom portu.** Pjesme zadano koriste port 8010;
   prije pokretanja provjeri što je zauzeto (`ss -tlnp` ili `docker ps`)
   i po potrebi promijeni lijevi broj porta u `docker-compose.yml`.
2. **Port 80 drži zajednički ulaz (nginx), a ne pojedina aplikacija.**
   Kad poželiš početnu stranicu s loginom i sve aplikacije iza nje:
   - u `docker-compose.yml` ograniči pjesme na localhost
     (`"127.0.0.1:8010:8000"`),
   - u nginxu dodaj `location /pjesme/` blok — gotov primjer je u
     komentaru na dnu `deploy/nginx-pjesme.conf` (aplikacija je spremna
     za rad iza prefiksa),
   - login najjednostavnije dodaš nginx *basic authom*
     (`auth_basic` + `htpasswd`) na razini cijelog server bloka.

---

## Firewall

Ako koristiš Hostingerov firewall (hPanel → VPS → *Firewall*) ili `ufw`,
otvori portove:

| Port | Namjena |
|------|---------|
| 22   | SSH     |
| 8010 | Pjesme Print (Docker, izravni pristup) |
| 80   | HTTP (portal/nginx) |
| 443  | HTTPS (ako koristiš certifikat) |

```bash
ufw allow 22 && ufw allow 8010 && ufw allow 80 && ufw allow 443
ufw enable
```

---

## Rješavanje problema

- **Stranica se ne otvara** — provjeri da servis radi:
  `docker compose ps` / `systemctl status pjesme`, pa firewall (gore).
- **413 Request Entity Too Large** — povećaj `client_max_body_size` u nginx
  konfiguraciji i/ili `MAX_UPLOAD_MB` (environment varijabla aplikacije).
- **Konverzija traje dugo / timeout** — veliki PDF-ovi mogu trajati; timeout
  je postavljen na 300 s u gunicornu i nginxu. Po potrebi povećaj oboje.
- **Logovi** — Docker: `docker compose logs -f`;
  systemd: `journalctl -u pjesme -f`.
