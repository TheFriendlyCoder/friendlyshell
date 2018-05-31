"""Common shell interaction logic shared between different shells"""
from __future__ import print_function
import os
import sys
import inspect
import subprocess
import pyparsing as pp
from six.moves import input
from friendlyshell.command_parsers import default_line_parser

# Path where configuration data is stored for friendly shells
CONFIG_FOLDER = os.path.expanduser(os.path.join("~", ".friendlyshell"))


# pylint: disable=no-member
class BaseShell(object):
    """Common base class for all Friendly Shells

    Defines basic IO and interactive shell logic common to all Friendly Shells
    """
    def __init__(self, *args, **kwargs):
        super(BaseShell, self).__init__(*args, **kwargs)

        # characters preceding the cursor when prompting for command entry
        self.prompt = '> '

        # text to be displayed upon launch of the shell, before displaying
        # the interactive prompt
        self.banner_text = None

        # Flag indicating whether this shell should be closed after the current
        # command finishes processing
        self._done = False

        # Command parser API for parsing tokens from command lines
        self._parser = default_line_parser()

        # input redirection to use instead of the default stdin
        self._input_stream = None

        # parent Friendly Shell this shell runs under
        # only used for nested sub-shells
        self._parent = None

        # default comment delimiter
        self.comment_delimiter = "#"


    @property
    def _config_folder(self):
        """Gets the folder where config and log files should be stored

        :rtype: :class:`str`
        """
        # Create our config folder with restricted access to everyone but the
        # owner. This is just in case we write secrets to a log / history file
        # by accident then only the current user can see it.
        if not os.path.exists(CONFIG_FOLDER):
            os.makedirs(CONFIG_FOLDER, 0o700)

        return CONFIG_FOLDER

    def _get_input(self):
        """Gets input to be processed from the appropriate source

        :returns: the input line retrieved from the source
        :rtype: :class:`str`
        """
        try:
            if self._input_stream:
                line = self._input_stream.readline()
                if not line:
                    raise EOFError()
                self.info(self.prompt + line)
            else:
                line = input(self.prompt)
            return line
        except KeyboardInterrupt:
            # When the user enters CTRL+C to terminate the shell, we just
            # terminate the currently running shell. That way if there is
            # a parent shell in play control can be returned to it so the
            # user can attempt to recover from whatever operation they
            # tried to abort
            self._done = True
            return None
        except EOFError:
            # When reading from an input stream, see if we've reached the
            # end of the stream. If so, assume we are meant to terminate
            # the shell and return control back to the caller. This avoids
            # having to force the user to always end their non-interactive
            # scripts with an 'exit' command at the end
            self.do_exit()
            return None
        except Exception as err:  # pylint: disable=broad-except
            self.error(
                'Unexpected error during input sequence: %s',
                err
            )

            # Reserve the detailed debug info / stack trace to the debug
            # output only. This avoids spitting out lots of technical
            # garbage to the user
            self.debug(err, exc_info=True)
            self._done = True
            return None

    def _execute_command(self, func, parser):
        """Calls a command function with a set of parsed parameters

        :param func: the command function to execute
        :param parser: The parsed command parameters to pass to the command
        """
        try:
            if not parser.params:
                func()
                return

            params_to_pass = parser.params
            num_params_total = self._count_params(func)
            if len(params_to_pass) > num_params_total:
                # If we have more tokens than parameters on the command function
                # we concatenate the extraneous tokens with the last parameter
                # assuming the command function is going to parse the tokens
                # itself or otherwise perform it's logic on the unparsed
                # input

                self.debug("too many tokens - concatenating extras")

                num_params_to_compress = \
                    len(params_to_pass) - num_params_total + 1
                self.debug("params to compress %s", num_params_to_compress)
                compressed = " ".join(params_to_pass[-num_params_to_compress:])
                self.debug("compressed params: %s", compressed)

                params_to_pass = params_to_pass[:-num_params_to_compress]
                params_to_pass.append(compressed)

            func(*params_to_pass)
        except Exception as err:  # pylint: disable=broad-except
            # Log summary info about the error to standard error output
            self.error('Unknown error detected: %s', err)
            # Reserve the detailed debug info / stack trace to the debug
            # output only. This avoids spitting out lots of technical
            # garbage to the user
            self.debug(err, exc_info=True)
        except KeyboardInterrupt:
            self.debug("User interrupted operation...")
            # Typically, when a user cancels an operation there will be at
            # least some partial output gemerated by the command so we
            # write out a blank to ensure the interactive prompt appears on
            # the line below
            self.info("")

    def do_native_shell(self, cmd):
        """Executes a shell command within the Friendly Shell environment"""
        self.debug("Running shell command %s", cmd)
        try:
            output = subprocess.check_output(
                cmd,
                shell=True,
                stderr=subprocess.STDOUT)
            self.info(output.decode("utf-8"))
        except subprocess.CalledProcessError as err:
            self.info("Failed to run command %s: %s", err.cmd, err.returncode)
            self.info(err.output)
        except KeyboardInterrupt:
            self.debug("User interrupted operation...")
            # Typically, when a user cancels an operation there will be at
            # least some partial output gemerated by the command so we
            # write out a blank to ensure the interactive prompt appears on
            # the line below
            self.info("")

    @staticmethod
    def alias_native_shell():
        """Gets the shorthand character for the 'native_shell' command

        :rtype: :class:`str`
        """
        return "!"

    def run_subshell(self, subshell):
        """Launches a child process for another shell under this one

        :param subshell: the new Friendly Shell to be launched"""
        subshell.run(input_stream=self._input_stream, parent=self)

    def run(self, *_args, **kwargs):
        """Main entry point function that launches our command line interpreter

        This method will wait for input to be given via the command line, and
        process each command provided until a request to terminate the shell is
        given.

        :param input_stream:
            optional Python input stream object where commands should be loaded
            from. Typically this will be a file-like object containing commands
            to be run, but any input stream object should work.
            If not provided, input will be read from stdin using :meth:`input`
        :param parent:
            Optional parent shell which owns this shell. If none provided this
            shell is assumed to be a parent or first level shell session with
            no ancestry
        """
        self._input_stream = \
            kwargs.pop("input_stream") if "input_stream" in kwargs else None
        self._parent = kwargs.pop("parent") if "parent" in kwargs else None

        if self.banner_text:
            self.info(self.banner_text)

        while not self._done:
            line = self._get_input()
            if not line:
                continue

            if line.startswith(self.comment_delimiter):
                self.debug("Skipping comment line %s", line)
                continue

            # Before we process our command input, see if we need to
            # substitute any environment variables that may be used
            line = os.path.expandvars(line)

            parser = self._parse_line(line)
            if parser is None:
                continue

            func = self._find_command(parser.command)
            if not func:
                self.error("Command not found: %s", parser.command)
                continue

            if not self._check_params(func, parser):
                continue

            self._execute_command(func, parser)

    def _check_params(self, func, parser):
        """Are there sufficient tokens to populate command parameters

        :param func: command function to be called
        :param parser: parsed tokens rom the shell
        :returns:
            true if there are sufficient parameters to call the command, false
            if not
        :rtype: :class:`bool`
        """
        num_tokens = len(parser.params) if parser.params else 0
        num_required_params = self._count_required_params(func)
        total_num_params = self._count_params(func)

        if total_num_params == 0 and num_tokens != 0:
            msg = "Command %s accepts no parameters but %s provided."
            self.error(
                msg,
                func.__name__.replace("do_", ""),
                num_tokens
            )
            return False

        if num_tokens < num_required_params:
            msg = 'Command %s requires %s parameters but %s provided.'
            self.error(
                msg,
                func.__name__.replace("do_", ""),
                num_required_params,
                num_tokens)
            return False
        return True

    def _count_required_params(self, cmd_method):
        """Gets the number of required parameters from a command method

        :param cmd_method:
            :class:`inspect.Signature` for method to analyse
        :returns:
            Number of required parameters (ie: parameters without default
            values) for the given method
        :rtype: :class:`int`
        """
        if sys.version_info < (3, 3):
            params = inspect.getargspec(cmd_method)  # pylint: disable=deprecated-method
            self.debug(
                'Command %s params are: %s',
                cmd_method.__name__,
                params)
            tmp = params.args

            if 'self' in tmp:
                tmp.remove('self')
            return len(tmp) - (len(params.defaults) if params.defaults else 0)

        func_sig = inspect.signature(cmd_method)  # pylint: disable=no-member
        retval = 0
        for cur_param in func_sig.parameters.values():
            if cur_param.default is inspect.Parameter.empty:  # pylint: disable=no-member
                retval += 1
        return retval

    def _count_params(self, cmd_method):
        """Gets the total number of parameters from a command method

        :param cmd_method:
            :class:`inspect.Signature` for method to analyse
        :returns:
            Number of parameters supported by the given method
        :rtype: :class:`int`
        """
        if sys.version_info < (3, 3):
            params = inspect.getargspec(cmd_method)  # pylint: disable=deprecated-method
            self.debug(
                'Command %s params are: %s',
                cmd_method.__name__,
                params)
            tmp = params.args

            if 'self' in tmp:
                tmp.remove('self')
            return len(tmp)

        func_sig = inspect.signature(cmd_method)  # pylint: disable=no-member
        return len(func_sig.parameters)

    def _parse_line(self, line):
        """Parses a single line of command text and returns the parsed output

        :param str line: line of command text to be parsed
        :returns: Parser object describing all of the parsed command tokens
        :rtype: :class:`pyparsing.ParseResults`"""
        self.debug('Parsing command input "%s"...', line)

        try:
            retval = self._parser.parseString(line, parseAll=True)
        except pp.ParseException as err:
            self.error('Parsing error:')
            self.error('\t%s', err.pstr)
            self.error('\t%s^', ' ' * (err.col-1))
            self.debug('Details: %s', err)
            return None
        self.debug('Parsed command line is "%s"', retval)
        return retval

    def _find_command(self, command_name):
        """Attempts to locate the command handler for a given command

        :param str command_name: The name of the command to find the handler for
        :returns: Reference to the method to be called to execute the command
                  Returns None if no command method found
        :rtype: :class:`meth`
        """
        self.debug("looking for command method...")

        # Gather all class methods, including static methods
        all_methods = inspect.getmembers(self, inspect.ismethod)
        all_methods.extend(inspect.getmembers(self, inspect.isfunction))

        # See if we can find a 'do_' method for our command...
        for cur_method in all_methods:
            self.debug("Processing %s", cur_method)
            if cur_method[0] == 'do_' + command_name:
                self.debug("command method found: %s", cur_method[0])
                return cur_method[1]

        # if no command method can be found for the specified token,
        # try looking up an alias for the command as well:
        self.debug("Looking for alias...")
        for cur_method in all_methods:
            if not cur_method[0].startswith("alias_"):
                continue
            self.debug("Found alias method %s", cur_method[0])
            if cur_method[1]() == command_name:
                orig_cmd_name = cur_method[0][len("alias_"):]
                self.debug("Recursing to find alias command %s", orig_cmd_name)
                return self._find_command(orig_cmd_name)

        self.debug("No command found with name " + command_name)
        return None

    def do_exit(self):
        """Terminates the command interpreter"""
        self.debug('Terminating interpreter...')
        self._done = True

        # See if our shell has any parents, and force them to quit too
        if self._parent:
            self._parent.do_exit()

    def do_close(self):
        """Terminates the currently running shell"""
        self.debug(
            'Closing shell %s (%s)',
            self.__class__.__name__,
            self.prompt)

        # Return control back to the parent Friendly Shell or the console,
        # whichever comes next in the shell's ancestry
        self._done = True

    @staticmethod
    def help_close():
        """Extended help for close method"""
        return """If the current shell is a sub-shell spawned by another """\
               """Friendly Shell instance, control will return to the """\
               """parent shell which will continue running"""

    @staticmethod
    def info(message, *args, **_kwargs):
        """Displays an info message to the default output stream

        Default implementation just directs output to stdout. Use a logging
        mixin class to customize this behavior.

        See :class:`friendlyshell.basic_logger_mixin.BasicLoggerMixin` for
        examples.

        :param str message: text to be displayed"""
        print(message % args)

    @staticmethod
    def warning(message, *args, **_kwargs):
        """Displays a non-critical warning message to the default output stream

        Default implementation just directs output to stdout. Use a logging
        mixin class to customize this behavior.

        See :class:`friendlyshell.basic_logger_mixin.BasicLoggerMixin` for
        examples.

         :param str message: text to be displayed"""
        print(message % args)

    @staticmethod
    def error(message, *args, **_kwargs):
        """Displays a critical error message to the default output stream

        Default implementation just directs output to stdout. Use a logging
        mixin class to customize this behavior.

        See :class:`friendlyshell.basic_logger_mixin.BasicLoggerMixin` for
        examples.

        :param str message: text to be displayed"""
        print(message % args)

    @staticmethod
    def debug(message, *args, **_kwargs):
        """Displays an internal-use-only debug message to verbose log file

        Default implementation hides all debug output. Use a logging mixin
        class to customize this behavior.

        See :class:`friendlyshell.basic_logger_mixin.BasicLoggerMixin` for
        examples.

        :param str message: text to be displayed"""
        pass


if __name__ == "__main__":
    pass
