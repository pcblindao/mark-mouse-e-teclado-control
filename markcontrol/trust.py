"""Fixação de identidades e pareamento no visualizador."""
from .certs import data_dir
from .config import lock, read_json, write_json


class Changed(ValueError):
    pass


class Trust:
    def __init__(self, directory=None):
        self.path = (directory or data_dir()) / "known_hosts.json"

    def records(self):
        hosts = {}
        for index, record in read_json(self.path, {}).items():
            fingerprint = record["fingerprint"]
            ip = record.get("ip") if index == fingerprint else index
            previous = hosts.get(fingerprint, {})
            hosts[fingerprint] = dict(record, ip=ip, key=record.get("key") or previous.get("key"))
        return hosts

    @staticmethod
    def check(hosts, peer, replace=False):
        if not replace and any(fp != peer.fingerprint and rec["ip"] == peer.ip
                               for fp, rec in hosts.items()):
            raise Changed("Este PC mudou de identidade.")

    def key(self, peer, replace=False):
        hosts = self.records()
        self.check(hosts, peer, replace)
        return hosts.get(peer.fingerprint, {}).get("key")

    def verified(self, peer, key=None, replace=False):
        # Chamado apenas depois de uma resposta HTTPS com SHA-256 verificado.
        with lock(self.path):
            hosts = self.records()
            self.check(hosts, peer, replace)
            if replace:
                for rec in hosts.values():
                    if rec["ip"] == peer.ip:
                        rec["ip"] = None
            fp = peer.fingerprint
            hosts[fp] = dict(ip=peer.ip, name=peer.name, fingerprint=fp,
                             key=key or hosts.get(fp, {}).get("key"))
            write_json(self.path, hosts)
