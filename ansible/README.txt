Copy this package into your existing C:\lab-agent\ansible directory.
Keep your current inventory.ini and ansible.cfg.

1) Create secrets:
   cp group_vars/vault.yml.example group_vars/vault.yml

2) Generate two mail password hashes:
   openssl passwd -6
   openssl passwd -6

3) Edit group_vars/vault.yml and set:
   - FTP passwords
   - the two generated mail hashes

4) Encrypt it:
   ansible-vault encrypt group_vars/vault.yml

5) Syntax check:
   ansible-playbook site.yml --syntax-check

6) Deploy by role:
   ansible-playbook site.yml --limit web --ask-vault-pass
   ansible-playbook site.yml --limit ftp --ask-vault-pass
   ansible-playbook site.yml --limit mail --ask-vault-pass
   ansible-playbook site.yml --limit dns --ask-vault-pass

7) Verify:
   ansible-playbook verify.yml --ask-vault-pass
