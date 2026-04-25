# proxysmart-vps


Ansible playbook for preparing a VPS for port forwarding from a Proxysmart server.

Steps on the VPS

`apt install ansible`


`cp -i vars.txt.example vars.txt`

edit the file ''vars.txt''

Replace ''PUBKEY'' with the **PUBKEY** . Save the file by pressing ''Control O'' and exit the editor by pressing ''Control x'' .

run:

`ansible-playbook ./proxysmart-vps.yml`

