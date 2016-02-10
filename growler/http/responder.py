#
# growler/http/responder.py
#
"""
The Growler class responsible for responding to HTTP requests.
"""

from .parser import Parser
from .request import HTTPRequest
from .response import HTTPResponse
from .methods import HTTPMethod

from .errors import (
    HTTPErrorBadRequest,
)


class GrowlerHTTPResponder():
    """
    The Growler Responder for HTTP connections.

    This class responds to client data by parsing the headers using the object
    created from the parser_factory parameter (defaults to
    growler.http.Parser). Upon completing headers the request and response
    objects are created and passed to the 'app' object found in protocol.

    This should only be constructed from a growler protocol instance.

    Parameters
    ----------
    protocol : GrowlerHTTPProtocol
        The GrowlerHTTPProtocol which created the responder.

    parser_factor : type or callable
        Factory function (or classname) of the object responsible for
        parsing the client's request line and headers. Default value is the
        growler.http.parser.Parser class. The object must have a 'consume'
        method which accepts the incoming data. If this data only has
        partial headers, consume returns None, and the parser should expect
        consume to be called again. When the headers have finished, the
        consume function returns any body data past the headers.

    request_factory : type or callable
        Factory function (or classname) of the request object which gets
        passed to the applications middleware as the first parameter. The
        default value is the class growler.http.request.HTTPRequest. This
        function accepts two arguments: the protocol handling the
        connection and the headers returned from the parser.

    response_factory : type or callable
        Factory function (or classname) of the response object which gets
        passed to the applications middleware as the second parameter. The
        default value is the class growler.http.response.HTTPResponse. This
        function accepts one argument: the protocol handling the
        connection.
    """

    body_buffer = None
    content_length = None
    headers = None

    def __init__(self,
                 protocol,
                 parser_factory=Parser,
                 request_factory=HTTPRequest,
                 response_factory=HTTPResponse,
                 ):
        self._proto = protocol
        self.parser = parser_factory(self)
        self.build_req = request_factory
        self.build_res = response_factory

    def on_data(self, data):
        """
        This is the function called by the http protocol upon receipt of
        incoming client data. The data is passed to the responder's parser
        class (via the consume method), which digests and stores as HTTP
        fields.

        Upon completion of parsing the HTTP headers, the responder creates the
        request and response objects, and passes them to the begin_application
        method, which starts the parent application's middleware chain.

        Parameters
        ----------
        data : bytes
            HTTP data from the socket, expected to be passed directly from the
            transport/protocol objects.
        """
        # Headers have not been read in yet
        if self.headers is None:
            # forward data to the parser
            data = self.parser.consume(data)

            # Headers are finished - build the request and response
            if data is not None:
                # builds request and response out of self.headers and protocol
                self.req, self.res = self.build_req_and_res()
                self.begin_application(self.req, self.res)

        # if truthy, 'data' now holds body data
        if data:
            self.validate_and_store_body_data(data)

            # if we have reached end of content - put in the request's body
            if self.content_length == self.headers['CONTENT-LENGTH']:
                self.req.body.set_result(b''.join(self.body_buffer))

    def begin_application(self, req, res):
        """
        Sends the given req/res objects to the application. To be called after
        parsing the request headers.
        """
        # Add the middleware processing to the event loop - this *should*
        # change the call stack so any server errors do not link back to this
        # function
        self.loop.create_task(self.app.handle_client_request(req, res))

    def set_request_line(self, method, url, version):
        """
        Sets the request line on the responder.
        """
        self.method = method
        self.parsed_request = (method, url, version)
        self._proto.request = {
            'method': method,
            'url': url,
            'version': version
        }
        if method in (HTTPMethod.POST, HTTPMethod.PUT):
            self.content_length = 0

    @property
    def method(self):
        """
        The HTTP method as an all-caps string (e.g. 'GET')
        """
        return self._proto.client_method

    @method.setter
    def method(self, method):
        """
        Sets the headers attribute and triggers the beginning of the req/res
        construction.
        """
        self._proto.client_method = method

    @property
    def parsed_query(self):
        """
        The HTTP query as parsed by the standard python urllib.parse library.
        """
        return self._proto.client_query

    @parsed_query.setter
    def parsed_query(self, value):
        """
        Stores the parsed query from the 'path' part of the client's request
        line. This value will be forwarded to the parent protocol object.
        """
        self._proto.client_query = value

    @property
    def headers(self):
        """
        The dict of HTTP headers.
        """
        return self._proto.client_headers

    @headers.setter
    def headers(self, header_dict):
        """
        Sets the headers attribute and triggers the beginning of the req/res
        construction.
        """
        self._proto.client_headers = header_dict

    def build_req_and_res(self):
        """
        Simple method which calls the request and response factories the
        responder was given, and returns the pair.
        """
        req = self.build_req(self._proto, self.headers)
        res = self.build_res(self._proto)
        return req, res

    def validate_and_store_body_data(self, data):
        """
        Attempts simple body data validation by comparining incoming data to
        the content length header. If passes store the data into self._buffer.

        Parameters
        ----------
        data : bytes
            Incoming client data to be added to the body

        Raises
        ------
        HTTPErrorBadRequest
            Raised if data is sent when not expected, or if too much data is
            sent
        """
        try:
            self.content_length += len(data)
            if self.content_length > self.headers['CONTENT-LENGTH']:
                problem = "Content length exceeds expected value (%d > %d)" % (
                    self.content_length, self.headers['CONTENT-LENGTH']
                )
                raise HTTPErrorBadRequest(phrase=problem)
        except (AttributeError, TypeError, KeyError):
            raise HTTPErrorBadRequest(phrase="Unexpected body data sent")

        self.body_buffer.append(data)

    @property
    def loop(self):
        """
        The asyncio event loop this responder belongs to.
        """
        return self._proto.loop

    @property
    def app(self):
        """
        The growler application this responder belongs to.
        """
        return self._proto.http_application
