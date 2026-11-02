"""The services a game's SDK opens through sm: (NX2Linux S7). Each is a horizon_hle.Service; a command not implemented here is logged by Horizon and answered with an error: nothing is faked.
install(hz) registers the named ports and the service table."""
import struct
from horizon_hle import Service, Response, Session, Event, OK, R_NOT_FOUND

SM_NOT_REGISTERED = 21 | 2 << 9


class ServiceManager(Service):
    name = 'sm:'
    def cmd_0(self, req): return Response(OK)   # Initialize (the process id travels in the message)

    def cmd_1(self, req):   # GetService(name: 8 bytes, zero padded)
        name = req.raw[:8].rstrip(b'\0').decode('ascii', 'replace')
        f = self.hz.services.get(name)
        if f is None:
            self.hz.log(f'sm: GetService({name!r}) -> not registered, asked by ' + self.hz.where(self.hz.regs))
            self.hz.missing_services.add(name)
            return Response(SM_NOT_REGISTERED)
        return Response(OK, objects=[Session(f(self.hz))])


class Logger(Service):
    """nn::lm ILogger"""
    name = 'lm:logger'
    def cmd_0(self, req):   # Log(buffer): the log packet; the text is in its payload chunks
        for kind, addr, size in req.bufs:
            if kind in ('A', 'X') and size:
                self.hz.log_packets.append(self.hz.rd(addr, min(size, 0x2000)))
        return Response(OK)
    def cmd_1(self, req): return Response(OK)   # SetDestination


class LogService(Service):
    name = 'lm'
    def cmd_0(self, req): return Response(OK, objects=[Session(Logger(self.hz))])   # OpenLogger(pid)


def install(hz):
    hz.named_ports['sm:'] = ServiceManager
    hz.services.update({'lm': LogService})
    hz.log_packets = []
    hz.missing_services = set()
