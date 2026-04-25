# proxysmart-vps


Ansible playbook for preparing a VPS for port forwarding from a Proxysmart server.

Steps on the VPS

`apt install ansible`


- put public ssh key from the Proxysmart server in `./proxysmart.ssh.pubkeys/` , 1 server == 1 file == 1 pub.key

`ansible-playbook ./proxysmart-vps.yml`

