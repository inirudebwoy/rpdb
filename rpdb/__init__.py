"""Remote Python Debugger (pdb wrapper)."""

__author__ = "Bertrand Janin <b@janin.com>"
__version__ = "0.1.6"

# import pdb
import socket
import threading
import sys
import traceback
from IPython.core.debugger import Pdb


class FileObjectWrapper(object):
    def __init__(self, fileobject, stdio):
        self._obj = fileobject
        self._io = stdio

    def __getattr__(self, attr):
        if hasattr(self._obj, attr):
            attr = getattr(self._obj, attr)
        elif hasattr(self._io, attr):
            attr = getattr(self._io, attr)
        else:
            raise AttributeError("Attribute %s is not found" % attr)
        return attr


class Rpdb(Pdb):

    def __init__(self, addr="127.0.0.1", port=4444):
        """Initialize the socket and initialize pdb."""

        # Backup stdin and stdout before replacing them by the socket handle
        self.old_stdout = sys.stdout
        self.old_stdin = sys.stdin
        self.port = port

        # Open a 'reusable' socket to let the webapp reload on the same port
        self.skt = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.skt.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
        self.skt.bind((addr, port))
        self.skt.listen(1)

        # Writes to stdout are forbidden in mod_wsgi environments
        try:
            sys.stderr.write("pdb is running on %s:%d\n"
                             % self.skt.getsockname())
        except IOError:
            pass

        (clientsocket, address) = self.skt.accept()
        handle = clientsocket.makefile('rw')

        Pdb.__init__(self, color_scheme='Linux', completekey='tab',
                     stdin=FileObjectWrapper(handle, self.old_stdin),
                     stdout=FileObjectWrapper(handle, self.old_stdin))
        handle.write("writing to handle")
        def import_module(possible_modules, needed_module):
            """Make it more resilient to different versions of IPython and try to
            find a module."""
            count = len(possible_modules)
            for module in possible_modules:
                sys.stderr.write(module)

                try:
                    return __import__(module, fromlist=[needed_module])
                except ImportError:
                    count -= 1
                    if count == 0:
                        raise

        possible_modules = ['IPython.terminal.ipapp',           # Newer IPython
                            'IPython.frontend.terminal.ipapp']  # Older IPython

        app = import_module(possible_modules, "TerminalIPythonApp")
        TerminalIPythonApp = app.TerminalIPythonApp

        possible_modules = ['IPython.terminal.embed',           # Newer IPython
                            'IPython.frontend.terminal.embed']  # Older IPython
        embed = import_module(possible_modules, "InteractiveShellEmbed")
        InteractiveShellEmbed = embed.InteractiveShellEmbed

        try:
            get_ipython
        except NameError:
            # Build a terminal app in order to force ipython to load the
            # configuration
            ipapp = TerminalIPythonApp()
            # Avoid output (banner, prints)
            ipapp.interact = False
            ipapp.initialize()
            def_colors = ipapp.shell.colors
        else:
            # If an instance of IPython is already running try to get an instance
            # of the application. If there is no TerminalIPythonApp instanciated
            # the instance method will create a new one without loading the config.
            # i.e: if we are in an embed instance we do not want to load the config.
            ipapp = TerminalIPythonApp.instance()
            shell = get_ipython()
            def_colors = shell.colors

            # Detect if embed shell or not and display a message
            if isinstance(shell, InteractiveShellEmbed):
                shell.write_err(
                    "\nYou are currently into an embedded ipython shell,\n"
                    "the configuration will not be loaded.\n\n"
                )

        self.rcLines += [line + '\n' for line in ipapp.exec_lines]
        sys.stdout = sys.stdin = handle
        OCCUPIED.claim(port, sys.stdout)
        sys.stderr.write(str(self.rcLines))

    def shutdown(self):
        """Revert stdin and stdout, close the socket."""
        sys.stdout = self.old_stdout
        sys.stdin = self.old_stdin
        OCCUPIED.unclaim(self.port)
        self.skt.close()

    def do_continue(self, arg):
        """Clean-up and do underlying continue."""
        try:
            return Pdb.do_continue(self, arg)
        finally:
            self.shutdown()

    do_c = do_cont = do_continue

    def do_quit(self, arg):
        """Clean-up and do underlying quit."""
        try:
            return Pdb.do_quit(self, arg)
        finally:
            self.shutdown()

    do_q = do_exit = do_quit

    def do_EOF(self, arg):
        """Clean-up and do underlying EOF."""
        try:
            return Pdb.do_EOF(self, arg)
        finally:
            self.shutdown()


def set_trace(addr="127.0.0.1", port=4444):
    """Wrapper function to keep the same import x; x.set_trace() interface.

    We catch all the possible exceptions from pdb and cleanup.

    """
    try:
        debugger = Rpdb(addr=addr, port=port)
    except socket.error:
        if OCCUPIED.is_claimed(port, sys.stdout):
            # rpdb is already on this port - good enough, let it go on:
            sys.stdout.write("(Recurrent rpdb invocation ignored)\n")
            return
        else:
            # Port occupied by something else.
            raise
    try:
        debugger.set_trace(sys._getframe().f_back)
        # debugger()
    except Exception:
        traceback.print_exc()


def post_mortem(addr="127.0.0.1", port=4444):

    debugger = Rpdb(addr=addr, port=port)
    type, value, tb = sys.exc_info()
    traceback.print_exc()
    debugger.reset()
    debugger.interaction(None, tb)


class OccupiedPorts(object):
    """Maintain rpdb port versus stdin/out file handles.

    Provides the means to determine whether or not a collision binding to a
    particular port is with an already operating rpdb session.

    Determination is according to whether a file handle is equal to what is
    registered against the specified port.
    """

    def __init__(self):
        self.lock = threading.RLock()
        self.claims = {}

    def claim(self, port, handle):
        self.lock.acquire(True)
        self.claims[port] = id(handle)
        self.lock.release()

    def is_claimed(self, port, handle):
        self.lock.acquire(True)
        got = (self.claims.get(port) == id(handle))
        self.lock.release()
        return got

    def unclaim(self, port):
        self.lock.acquire(True)
        del self.claims[port]
        self.lock.release()

# {port: sys.stdout} pairs to track recursive rpdb invocation on same port.
# This scheme doesn't interfere with recursive invocations on separate ports -
# useful, eg, for concurrently debugging separate threads.
OCCUPIED = OccupiedPorts()
