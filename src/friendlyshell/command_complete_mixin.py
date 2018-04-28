"""Mixin class that adds command completion to a friendly shell"""
import os
import inspect
import platform
import tempfile
from contextlib import contextmanager

try:  # pragma: no cover
    if platform.system() == 'Windows':
        import pyreadline.rlmain  # pylint: disable=unused-import
        import readline
        import pyreadline.unicode_helper  # pylint: disable=unused-import
    else:
        import readline

    # Truncate our history files to 1000 entries
    readline.set_history_length(1000)
    # readline.set_completer_delims(" ")
    AUTOCOMPLETE_ENABLED = True
except ImportError:  # pragma: no cover
    AUTOCOMPLETE_ENABLED = False


@contextmanager
def _autocomplete_helper(key, callback, history_file):  # pragma: no cover
    """Helper method used by :meth:`auto_complete_manager`

    This helper provides the functionality for a "simple", unnested
    auto-completion system. For Friendly Shells without nested sub-shells
    this helper provides all the functionality necessary to support command
    completion.

    For nested sub-shells to parent :meth:`auto_complete_manager` method
    takes care of swizzling the command history for each nested sub-shell
    so each one can have it's own independent history and such independent
    from the parent and it's siblings.


    :param str key: see :meth:`_autocomplete_helper` for details
    :param callback: see :meth:`_autocomplete_helper` for details
    :param str history_file: see :meth:`_autocomplete_helper` for details
    """
    if os.path.exists(history_file) and hasattr(readline, "read_history_file"):
        readline.read_history_file(history_file)

    # Configure our auto-completion callback
    old_completer = readline.get_completer()
    readline.set_completer(callback)
    readline.parse_and_bind(key + ": complete")

    # Return control back to the caller
    try:
        yield
    finally:
        # When the context manager goes out of scope,
        # restore it's previous state
        readline.set_completer(old_completer)

        # When finished, write our history to a file
        if history_file:
            readline.write_history_file(history_file)


@contextmanager
def auto_complete_manager(key, callback, history_file=None):  # pragma: no cover
    """Context manager for enabling command line auto-completion

    This context manager can be used to ensure that command completion for
    a Friendly Shell runner can have it's own self-defined auto-completion
    and command history while not affecting the global completion sub-system
    that may already be configured prior to running the Friendly Shell.
    Through the use of a context manager, we ensure the state of the
    command completion subsystem will be restored regardless of how the
    context manager was terminated.

    :param str key:
        descriptor for keyboard key to use for auto completion trigger
    :param callback:
        method point for the callback to run when completion key is pressed
    :param str history_file:
        optional path to the history file to use for storing previous commands
        run by this shell. If not provided, history will not be saved.
    """
    # If auto-completion isn't supported, do nothing
    if not AUTOCOMPLETE_ENABLED:
        yield
        return

    # If auto-complet enabled but no existing history exists, we can assume
    # we aren't working inside a nested sub-shell, so just return our
    # conventional context manager
    if not readline.get_current_history_length():
        with _autocomplete_helper(key, callback, history_file):
            yield
            return

    # If we get here we know we're inside a nest sub-shell, so we need
    # to take care to preserve the state of the parent shell so the sub-shell
    # can initialize it's own unique history
    with tempfile.NamedTemporaryFile() as temp_file:

        # Save the current shell's history to a temporary file
        readline.write_history_file(temp_file.name)
        readline.clear_history()

        # Then launch our typical auto-complete context manager
        with _autocomplete_helper(key, callback, history_file):
            yield

        # restore the state of our command history for the parent shell
        # when the sub-shell terminates
        readline.clear_history()
        readline.read_history_file(temp_file.name)


# pylint: disable=too-few-public-methods
class CommandCompleteMixin(object):  # pragma: no cover
    """Mixin to be added to any friendly shell to add command completion"""

    def __init__(self, *args, **kwargs):
        super(CommandCompleteMixin, self).__init__(*args, **kwargs)
        self.complete_key = 'tab'
        self._latest_matches = None

    def _complete_command_names(self, partial_cmd):
        """Autocompletion method for command names"""
        self.debug("complete_command_names...")
        all_methods = inspect.getmembers(self, inspect.ismethod)
        retval = []
        for cur_method in all_methods:
            if cur_method[0].startswith('do_' + partial_cmd):
                retval.append(cur_method[0][3:])
        self.debug("Completions for available commands: %s", retval)
        return retval

    def _get_completion_callback(self, cmd):
        """Finds callback for completing command parameters for a given command
        """
        self.debug("get_completion_callback...")
        # Next we check to see if the specified command has an auto-completion
        # method. As a general convention we assume such helper methods are
        # named with the prefix "complete_".
        method_name = 'complete_' + cmd
        if not hasattr(self, method_name):
            self.debug('No completion method for command %s', cmd)
            return None

        # Make sure the auto-completion method looks correct (ie: is callable)
        tmp_method = getattr(self, method_name)
        if not inspect.ismethod(tmp_method):
            self.debug(
                '\tCompletion method %s should be callable with 2 '
                'input parameters. Check derived class.')
            return None

        return tmp_method

    def _get_callback_param_index(self, parser, original_line, token):
        """Calculates which command parameter is being completed."""
        self.debug("get_callback_param_index")
        param_index = None
        for i in range(len(parser.params)):
            self.debug(
                "\tSeeing if token %s is the one to match", parser.params[i])
            self.debug("\t\tMatching offset is %s", readline.get_begidx())
            self.debug("\t\tMatching token is %s", token)
            self.debug(
                "\t\tOffset of %s in %s is %s",
                parser.params[i],
                original_line,
                original_line.find(parser.params[i], len(parser.command)))
            if original_line.find(parser.params[i], len(parser.command)) == \
                    readline.get_begidx():
                self.debug("\tFound match at parameter %s", i)
                param_index = i
                break

        # Sanity checks...
        if param_index is None:
            self.debug('Unable to match param %s to index', token)
            return None

        if not parser.params[param_index].startswith(token):
            self.debug(
                'Messed up token matching %s != %s',
                token,
                parser.params[param_index])
            return None

        self.debug(
            "Parameter being completed is %s: %s",
            param_index,
            parser.params[param_index])
        return param_index

    def _get_completions(self, tmp_method, parser, param_index, token):
        """Gets a list of possible matches for a given command parameter"""
        self.debug(
            '\tCalling into auto completion method %s...', tmp_method.__name__)
        retval = tmp_method(parser.params, param_index, len(token))
        self.debug('Found matches: %s', retval)

        # Sanity Check: command completion methods MUST always return a list of
        # possible token matches. The list may be empty, but they must always
        # return a list
        if not isinstance(retval, list):
            self.debug(
                '\tUser defined completion method %s must return a list of '
                'matches',
                tmp_method.__name__)
            self.debug('\tActual returned data was %s', retval)
            return None

        return retval

    def _get_completion_matches(self, token):
        """get a list of potential matches for a incomplete token

        :param str token: token to be matched
        :returns:
            list of 0 or more compatible tokens that partially match
            the one given
        :rtype: :class:`list`
        """
        self.debug("get_completion_matches...")

        # Get the full input line as it appears on the prompt
        original_line = readline.get_line_buffer()

        # If the start of our current token is the start of the line, we can
        # assume the user wants us to complete the name of one of the Friendly
        # Shell's commands (since all commands begin with a command name)
        # Therefore, we simply return a list of command names that partially
        # match the current token
        if readline.get_begidx() == 0:
            self.debug("Token start is at index 0 - completing command...")
            return self._complete_command_names(token)

        # Once here, we know we've at least got a command name and the user
        # now wishes to auto-complete one of the parameters to that command. So
        # we next parse our partially entered command to get more contextual
        # information
        parser = self._parse_line(original_line)

        self.debug("Parsed completion line: %s", parser)
        tmp_method = self._get_completion_callback(parser.command)
        if not tmp_method:
            self.debug("No completion callback method found")
            return None
        self.debug("Found completion callback method: %s", tmp_method.__name__)

        # Figure out which of our parsed command tokens is the one to be
        # auto-completed
        param_index = \
            self._get_callback_param_index(parser, original_line, token)
        if param_index is None:
            self.debug("Token could not be detected in parameter list")
            return None
        self.debug("Parameter %s contains the completion token", param_index)

        # Call our auto-completion helper method to get a list of possible
        # matches to the partially entered parameter
        return self._get_completions(tmp_method, parser, param_index, token)

    def _complete_callback(self, token, index):
        """Autocomplete method called by readline library to retrieve candidates
         for a partially entered command

        NOTE: Exceptions and errors in this callback, including returning of
        "invalid" data like :class:`None` are simply ignored and treated as
        though there were no matches found. As such there doesn't appear to be
        any way to force the interpreter to exit when errors occur in this
        method.

        NOTE: Seeing as how any output to stdout or stderr result in corruption
        of the interactive prompt which uses this callback, all output messages
        produced by this method are redirected to debug streams so they can be
        silently logged to disk for later diagnostics.

        :param str token:
            the text associated with the token that is to be completed
        :param int index:
            index of which matching result from the possible list of matching
            tokens to return. So for example 0 means return the first potential
            match for the given token from the list of compatible matches. 1
            means return the second potential match, etc.
        :returns:
            Full text for the given token which partially matches the text of
            the currently selected token
            Returns None if there are no matches for the given token
        """
        try:
            line = readline.get_line_buffer()
            # ------------------------- DEBUG OUTPUT ---------------------------
            # NOTE: The begidx and endidx parameters specify the start and end+1
            #       location of the sub-string
            #       being processed
            # NOTE: If the user has placed their input cursor in the middle of
            #       the token, only the characters up to but not including the
            #       one above the cursor are returned in this parameter
            if index == 0:
                self.debug('Beginning auto-completion routine...')

            self.debug('\t\tSelected token "%s"', token)
            self.debug('\t\tMatch to return "%s"', index)
            # All text currently entered at the prompt, less the prompt itself
            self.debug('\t\tline "%s"', line)
            # represents the offset from the start of the string to the first
            # character in the token to process
            self.debug('\t\tBeginning index "%s"', readline.get_begidx())
            # represents the offset from the start of the string to the
            # character under the cursor
            # NOTE: this may be the end of the current token or it may not...
            # NOTE: if the cursor is past the end of the last token
            # (ie: preparing to accept a new character)
            #       this index would be: len(line) + 1
            self.debug('\t\tEnding index "%s"', readline.get_endidx())
            # ------------------------------------------------------------------
            if readline.get_line_buffer()[0] == "!":
                self.debug(
                    "Processing subcommand '%s'. Skipping command expansion.",
                    line)
            if index != 0:
                if index >= len(self._latest_matches):
                    self.debug('Completed auto completion routine.')
                    return None

                self.debug(
                    '\tReturning partial match #%s: %s',
                    index,
                    self._latest_matches[index]
                )
                return self._latest_matches[index]

            self._latest_matches = self._get_completion_matches(token)
            self.debug("Possible matches are: %s", self._latest_matches)

            if self._latest_matches is None:
                self.debug(
                    '\tFailed to get completion matches for token %s',
                    token
                )
                return None

            assert isinstance(self._latest_matches, list)

            if not self._latest_matches:
                self.debug('\tNo matches for token %s found', token)
                return None

            self.debug(
                '\tReturning first match %s', self._latest_matches[0])
            return self._latest_matches[0]

        except Exception as err:  # pylint: disable=broad-except
            self.debug(
                'Unknown error during command completion operation: %s',
                err,
                exec_info=True
            )
            return None  # pylint: disable=lost-exception

    def do_clear_history(self):
        """Clears the history of previously used commands from this shell"""
        if not AUTOCOMPLETE_ENABLED:
            self.info("Command completion disabled.")
            return

        # We just clear the current history buffer. When the shell terminates
        # it should write the history to the history file, which should write
        # out an empty history file with maybe just an 'exit' command in it
        readline.clear_history()


if __name__ == "__main__":
    pass
