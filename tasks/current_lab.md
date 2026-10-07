\# CURRENT LAB - WIRELESS SECURITY LAB 08



\## Scope



Configure ONLY these servers:



radius1

dhcp1



Do not configure routers, switches or access points yet.



\## Group



Group number: 5



Network:



172.18.15.0/24



Lab addressing:



Gateway:

172.18.15.1



FreeRADIUS:

172.18.15.10



DHCP server:

172.18.15.20



Archer C20:

172.18.15.30



Archer BE700:

172.18.15.40





\# IMPORTANT NETWORK RULE



The servers already have static IP addresses.



DO NOT change:



\- IP addresses

\- interface configuration

\- default gateway

\- DNS

\- netplan



unless the user explicitly approves it.



During discovery verify:



ip -br -4 addr

ip route

ping between servers

gateway reachability

Internet reachability



If the configured gateway does not work, STOP and report the problem.





\# RADIUS SERVER



Device:



radius1



Required software:



freeradius

freeradius-utils





\## Installation



Inspect first.



If packages are missing:



apt update

apt install freeradius freeradius-utils -y



Enable and start FreeRADIUS.





\## Backup



Before modifying FreeRADIUS configuration create:



/etc/freeradius/3.0.bak-<timestamp>





\## RADIUS NAS clients



Configure:



client archer\_c20 {

&#x20;   ipaddr = 172.18.15.30

&#x20;   secret = 5PTecsup2

&#x20;   shortname = C20-WIFI5

}



client archer\_be700 {

&#x20;   ipaddr = 172.18.15.40

&#x20;   secret = 5PTecsup2

&#x20;   shortname = BE700-WIFI7

}





\## RADIUS local users



Configure this laboratory example account:



jalvarez Cleartext-Password := "admin123"

&#x20;   Reply-Message := "Acceso autorizado - docente"



A second real group member account must also be configured.



STOP and ask the user for:



\- RADIUS username

\- RADIUS password



before creating the second account.





\## EAP



Inspect:



/etc/freeradius/3.0/mods-enabled/eap



Verify PEAP and MSCHAPv2 are available.



Do not modify the default EAP configuration unless inspection proves

that a required setting is missing.





\## Validation



Before restarting FreeRADIUS ALWAYS run:



freeradius -XC



Only restart if validation succeeds.



Then verify:



systemctl status freeradius --no-pager



ss -lunp



Ports expected:



1812/UDP

1813/UDP





\## Authentication tests



Test valid credentials:



radtest jalvarez admin123 127.0.0.1 0 testing123



Expected:



Access-Accept



Then deliberately test an incorrect password.



Expected:



Access-Reject



Capture/report the relevant output.





\# DHCP SERVER



Device:



dhcp1



The laboratory document defines the DHCP server address but does not

specify a DHCP software package or exact pool.



For this simulation use ISC DHCP Server unless another DHCP server is

already installed and working.



Required package:



isc-dhcp-server





\## DHCP network



Network:

172.18.15.0



Netmask:

255.255.255.0



Proposed simulation pool:



172.18.15.100 - 172.18.15.200



The pool deliberately avoids:



172.18.15.1   gateway

172.18.15.10  RADIUS

172.18.15.20  DHCP

172.18.15.30  Archer C20

172.18.15.40  Archer BE700



Gateway offered to clients:



172.18.15.1



DNS:



8.8.8.8

1.1.1.1





\## DHCP configuration



Detect the network interface containing:



172.18.15.20



Do not assume that the interface is ens33.



Configure ISC DHCP Server to listen ONLY on that interface.



Configure:



subnet 172.18.15.0 netmask 255.255.255.0



range:



172.18.15.100 172.18.15.200



router:



172.18.15.1



broadcast:



172.18.15.255



DNS:



8.8.8.8, 1.1.1.1



default lease:



600 seconds



maximum lease:



7200 seconds





\## Backup



Before editing:



/etc/dhcp/dhcpd.conf



and:



/etc/default/isc-dhcp-server



create timestamped backups.





\## Validation



Before restarting run:



dhcpd -t -cf /etc/dhcp/dhcpd.conf



Only restart DHCP if syntax validation succeeds.



Then verify:



systemctl status isc-dhcp-server --no-pager



journalctl -u isc-dhcp-server -n 50 --no-pager



ss -lunp



Expected DHCP server port:



UDP 67





\# GENERAL AGENT BEHAVIOR



Before making changes perform READ-ONLY discovery.



Create backups before configuration changes.



Use:



.\\labctl.cmd exec



for normal commands.



Use:



.\\labctl.cmd sudo-exec



only when root privileges are required.



Use:



.\\labctl.cmd upload



when transferring generated configuration files.



If a command fails:



1\. inspect complete output

2\. inspect service status

3\. inspect logs

4\. diagnose cause

5\. apply smallest correction

6\. validate

7\. retest



Do not claim PASS without verification.



At completion provide:



\- configuration performed

\- files modified

\- backups created

\- service status

\- listening ports

\- RADIUS Access-Accept result

\- RADIUS Access-Reject result

\- DHCP syntax validation

\- DHCP service status

\- any issue requiring physical/user intervention

