# Porter on one EC2 instance, by hand

**Copy this folder to the instance with `scp`, log in with `ssh`, and run the same `docker compose up` as on the laptop. That's the whole deploy.**

```
this laptop ── ssh · scp, port 22 ──► the instance: ~/v2 ── docker compose up ──► porter :8080 · postgres · prometheus · grafana
a browser, anywhere ── http, port 8080 ──► porter
```

- Every command runs **on the laptop, from `part_2/v2`**, unless it says *on the server*.
- The `.sh` files beside this README do the same steps for you. This page spells them out.

## Fill in once

Put the instance's address on the first line, then paste both lines into the terminal. Do it again in every new terminal you open.

```bash
IP=PASTE_THE_ADDRESS_HERE              # the instance's Public IPv4 address, e.g. 13.201.45.67: no http://, no :8080
KEY=$HOME/.ssh/porter-demo.pem         # the private key, saved when the key pair was made
```

## 0 · The instance (once)

In the EC2 console, in the region you want (top right), **Launch instance**:

| setting | value |
|---|---|
| Name | `porter-demo` |
| Image | Ubuntu Server 24.04 LTS, **64-bit (Arm)** |
| Instance type | `t4g.small` (2 vCPUs, 2 GB, about two US cents an hour) |
| Key pair | Create new key pair → `porter-demo`, RSA, `.pem`. The browser downloads `porter-demo.pem` |
| Network settings | Edit → Create security group. Rule 1: SSH, source type **My IP**. Add security group rule: Custom TCP, port **8080**, source type **Anywhere** |
| Storage | 20 GiB |

When it says *Running*, copy its **Public IPv4 address** into `IP=` above. Then put the key where ssh expects it, readable by you alone (ssh refuses a key anyone else can read):

```bash
mv ~/Downloads/porter-demo.pem ~/.ssh/
chmod 400 ~/.ssh/porter-demo.pem
```

- `./deploy/create.sh` does all of step 0 from the terminal instead, installs Docker too (step 2), and prints the address.

## 1 · Log in

```bash
ssh -i $KEY ubuntu@$IP
```

- `ubuntu` is the user on Ubuntu images. The first time, ssh asks whether to trust the server: type `yes`.
- `exit` logs out.

## 2 · Docker on the server (once)

*On the server*, after step 1:

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 docker-buildx    # Docker, `docker compose`, and buildx, which builds the two-stage Dockerfile
sudo usermod -aG docker ubuntu                                        # ubuntu may run docker without sudo, from the next login

# 2 GB of swap: building the image on a 2 GB machine can run out of memory without it
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

exit                                                                  # log out, so the next login is in the docker group
```

## 3 · Copy this folder up

```bash
scp -i $KEY -r ../v2 ubuntu@$IP:~
```

- `-r` copies the folder and everything in it, `.env` and `online_retail.db` included. On the server it is `~/v2`.
- **`.env` holds the OpenAI key.** It travels inside the ssh connection, and Compose hands it to the container there. The image never holds it.
- Run it again after any change: it overwrites what's on the server.

## 4 · Start it

```bash
ssh -i $KEY ubuntu@$IP
```

*On the server:*

```bash
cd v2
docker compose up -d --build     # build the image here, start all four containers: the laptop's P3 command
docker compose ps                # porter, postgres, prometheus and grafana, all Up
exit
```

- The first build takes a few minutes on the instance. After that, only what changed is rebuilt.

## 5 · From the laptop: is it up?

```bash
curl http://$IP:8080/healthz
curl http://$IP:8080/v1/chat -H 'content-type: application/json' -d '{"question": "When did I place order 574694?", "customer_id": 12381}'
```

- **In a browser, a phone too:** `http://<the address>:8080` is the chat page, `http://<the address>:8080/staff` the returns desk.

## 6 · Prometheus and Grafana, through an ssh tunnel

On the server they listen on its own address only (`127.0.0.1` in `compose.yaml`), and the security group lets in nothing but 22 and 8080. An ssh tunnel brings them to this laptop:

```bash
docker compose stop prometheus grafana       # only if they're running on the laptop: frees ports 9090 and 3000
ssh -i $KEY -N -L 9090:127.0.0.1:9090 -L 3000:127.0.0.1:3000 ubuntu@$IP
```

- **`-L 9090:127.0.0.1:9090`** sends this laptop's port 9090, inside the ssh connection, to port 9090 on the server. **`-N`**: no shell, only the tunnel.
- It prints nothing and keeps running: leave it. <http://localhost:9090> and <http://localhost:3000> are now the server's Prometheus and Grafana.
- Ctrl+C closes it.

## 7 · Day to day

```bash
ssh -i $KEY ubuntu@$IP "cd v2 && docker compose ps"                       # what's running
ssh -i $KEY ubuntu@$IP "cd v2 && docker compose logs --tail 20 porter"    # the service's last log lines
```

After changing the code, copy `porter/` alone, then rebuild:

```bash
scp -i $KEY -r porter ubuntu@$IP:~/v2/
ssh -i $KEY ubuntu@$IP "cd v2 && docker compose up -d --build"
```

- With a command in quotes after the address, ssh runs that one command on the server and comes back.

## 8 · When you're done

```bash
ssh -i $KEY ubuntu@$IP "cd v2 && docker compose down"     # the containers stop
```

- **Then stop the cost:** in the EC2 console, select the instance → Instance state → **Terminate**. Its disk goes with it.
- Delete the `porter-demo` security group and key pair too if you won't use them again.
- `./deploy/destroy.sh` does all three, if `create.sh` made them.

## If something goes wrong

- **`Permission denied (publickey)`:** the wrong key, or not the `ubuntu` user.
- **`UNPROTECTED PRIVATE KEY FILE`:** `chmod 400 $KEY`.
- **`Could not resolve hostname`:** `IP` isn't set in this terminal, or it has `http://` or `:8080` in it.
- **ssh hangs, then times out:** the security group lets ssh in only from the address this laptop had when the rule was made. On a new network: EC2 console → the security group → Edit inbound rules → the SSH rule → My IP.
- **No Public IPv4 address on the instance:** Network settings → Auto-assign public IP must be Enable.
- **`permission denied while trying to connect to the Docker daemon socket`:** the login is older than the `usermod`. `exit`, and ssh in again.
- **The address changed:** an instance that is stopped and started gets a new public IP. Copy the new one into `IP=`.
- **The tunnel says "Address already in use":** the laptop's Prometheus or Grafana still holds 9090 or 3000: `docker compose stop prometheus grafana`.
