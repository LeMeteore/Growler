#
# growler/app.py
#
"""
Defines the base application (App) that defines a 'growlerific' program. This
is where the developers should start writing their own application, as all of
the HTTP handling is done elsewhere. The typical use case is a single instance
of an application which is the endpoint for a (or multiple) webserver.

Currently the only webserver which can forward requests to a Growler App is the
growler webserver packaged, but it would be nice to expand this to other
popular web frameworks.

A simple app can be created raw (no subclassing) and then decorate functions or
a class to modify the behavior of the app. (decorators explained elsewhere)

app = App()

@app.use
def myfunc(req, res):
    print("myfunc")

"""

import asyncio
import os

from .http import (
    HTTPRequest,
    HTTPResponse,
    HTTPError,
    HTTPErrorInternalServerError,
    HTTPErrorNotFound
)
from .router import Router


class App(object):
    """
    A Growler application object. You can use a 'raw' app and modify it by
    decorating functions and objects with the app object, or subclass App and
    define request handling and middleware from within.

    The typical use case is a single App instance which is the end-point for a
    dedicated webserver. Upon each connection from a client, the
    _handle_connection method is called, which constructs the request and
    response objects from the asyncio.Stream{Reader,Writer}. The app then
    proceeds to pass these req and res objects to each middleware object in its
    chain, which may modify either. The default behavior is to have a
    growler.router as the last middleware in the chain and finds the first
    pattern which matches the req.path variable, and calls this function.

    If at any point in the middleware chain, res is found to be in the 'closed'
    state, we simply stop the chain. If after the last middleware is called and
    the res is NOT in the 'closed' state, an error will be thrown. If at any
    point in the middleware chain an exception is thrown, the exception type is
    searched for in a chain of error handlers, and if found is called, with the
    expectation that res will be closed. If it is NOT closed at this point or
    if a handler was not found, the default implementation throws a '500 -
    Server Error' to the user.

    Each middleware in the chain can either be a normal function or an
    asyncio.coroutine. Either will be called asynchronously. There is no
    timeout variable yet, but I think I will put one in later, to ensure that
    the middleware is as responsive as the dev expects.
    """

    def __init__(self,
                 name=__name__,
                 loop=asyncio.get_event_loop(),
                 no_default_router=False,
                 debug=True,
                 request_class=HTTPRequest,
                 response_class=HTTPResponse,
                 **kw
                 ):
        """
        Creates an application object.

        @param name: does nothing right now
        @type name: str

        @param loop: The event loop to run on
        @type loop: asyncio.AbstractEventLoop

        @param debug: (de)Activates the loop's debug setting
        @type debug: boolean

        @param request_class: The factory of request objects, the default of
            which is growler.HTTPRequest. This should only be set in special
            cases, like debugging or if the dev doesn't want to modify default
            request objects via middleware.
        @type request_class: runnable

        @param response_class: The factory of response objects, the default of
            which is growler.HTTPResponse. This should only be set in special
            cases, like debugging or if the dev doesn't want to modify default
            response objects via middleware.
        @type response_class: runnable

        @param kw: Any other custom variables for the application. This dict is
            stored as 'self.config' in the application. These variables are
            accessible by the application's dict-access, as in:
                app = app(..., val='VALUE')
                app['var'] #=> val

        @type kw: dict

        """
        self.name = name
        self.cache = {}

        self.config = kw

        # rendering engines
        self.engines = {}
        self.patterns = []
        self.loop = loop
        self.loop.set_debug(debug)

        self.middleware = []  # [{'path': None, 'cb' : self._middleware_boot}]

        # set the default router
        self.routers = [] if no_default_router else [Router('/')]

        self.enable('x-powered-by')
        self.set('env', os.getenv('GROWLER_ENV', 'development'))

        self._on_connection = []
        self._on_headers = []
        self._on_error = []
        self._on_http_error = []

        self._wait_for = [asyncio.sleep(0.1)]

        self._request_class = request_class
        self._response_class = response_class

    def __call__(self, req, res):
        """
        Calls the growler server with the request and response objects.
        """
        print("Calling growler", req, res)

    @asyncio.coroutine
    def _handle_connection(self, reader, writer):
        """
        Called upon a connection from remote client. This is the default
        behavior if application is run using '_server_listen' method. Request
        and response objects are created from the stream reader/writer and
        middleware is cycled through and applied to each. Changing behavior of
        the server should be handled using middleware and NOT overloading
        _handle_connection.

        @type reader: asyncio.StreamReader
        @type writer: asyncio.StreamWriter
        """

        print('[_handle_connection]', self, reader, writer, "\n")

        # Call each action for the event 'OnConnection'
        for f in self._on_connection:
            f(reader._transport)

        # create the request object
        req = self._request_class(reader, self)

        # create the response object
        res = self._response_class(writer, self)

        # create an asynchronous task to process the request
        processing_task = asyncio.Task(req.process())

        try:
            # run task
            yield from processing_task
        # Caught an HTTP Error - handle by running through HTTPError handlers
        except HTTPError as err:
            processing_task.cancel()
            err.PrintSysMessage()
            print(err)
            for f in self._on_http_error:
                f(err, req, res)
            return
        except Exception as err:
            processing_task.cancel()
            print("[Growler::App::_handle_connection] Caught Exception ")
            print(err)
            for f in self._on_error:
                f(err, req, res)
            return

        # Call each action for the event 'OnHeaders'
        for f in self._on_headers:
            yield from self._call_and_handle_error(f, req, res)

            if res.has_ended:
                print("[OnHeaders] Res has ended.")
                return

        # Loop through middleware
        for md in self.middleware:
            print("Running Middleware : ", md)

            yield from self._call_and_handle_error(md, req, res)

            if res.has_ended:
                print("[middleware] Res has ended.")
                return

        route_generator = self.routers[0].match_routes(req)
        for route in route_generator:
            waitforme = asyncio.Future()
            if not route:
                raise HTTPErrorInternalServerError()

        yield from self._call_and_handle_error(route, req, res)

        if res.has_ended:
            print("[Route] Res has ended.")
            return
        else:
            yield from waitforme

        # Default
        if not res.has_ended:
            e = Exception("Routes didn't finish!")
            for f in self._on_error:
                f(e, req, res)

    def _call_and_handle_error(self, func, req, res):

        def cofunctitize(_func):
            @asyncio.coroutine
            def cowrap(_req, _res):
                return _func(_req, _res)
            return cowrap

        # Provided middleware is a 'normal' function - we just wrap with the
        # local 'cofunction'
        if not (asyncio.iscoroutinefunction(func) or
                asyncio.iscoroutine(func)):
            func = cofunctitize(func)

        try:
            yield from func(req, res)
        except HTTPError as err:
            # func.cancel()
            err.PrintSysMessage()
            print(err)
            for f in self._on_http_error:
                f(err, req, res)
            return
        except Exception as err:
            # func.cancel()
            print("[Growler::App::_handle_connection] Caught Exception ")
            print(err)
            for f in self._on_error:
                f(err, req, res)
            return

    def onstart(self, cb):
        print("Callback : ", cb)
        self._on_start.append(cb)

    @asyncio.coroutine
    def wait_for_required(self):
        """
        Called before running the server, ensures all required coroutines have
        finished running.
        """
        # print("[wait_for_all] Begin ", self._wait_for)

        for x in self._wait_for:
            yield from x

    #
    # Middleware adding functions
    #
    # These functions can be called explicity or decorate functions. They are
    # forwarding functions which call the same function name on the root
    # router.
    # These could be assigned on construction using the form:
    #
    #    self.all = self.routers[0].all
    #
    # , but that would not allow the user to switch the root router (easily)
    #

    def all(self, path="/", middleware=None):
        """
        An alias of the default router's 'all' method. The middleware provided
        is called upon any HTTP request that matching the path, regardless of
        the method.
        """
        return self.routers[0].all(path, middleware)

    def get(self, path="/", middleware=None):
        """
        An alias call for simple access to the default router. The middleware
        provided is called upon any HTTP 'GET' request which matches the path.
        """
        if middleware is None:
            return self.routers[0].get(path, middleware)
        self.routers[0].get(path, middleware)

    def post(self, path="/", middleware=None):
        """
        An alias of the default router's 'post' method. The middleware provided
        is called upon a POST HTTP request matching the path.
        """
        return self.routers[0].post(path, middleware)

    def use(self, middleware, path=None):
        """
        Use the middleware (a callable with parameters res, req, next) upon
        requests match the provided path. A None path matches every request.
        Returns 'self' so the middleware may be nicely chained.
        """
        print("[App::use] Adding middleware", middleware)
        self.middleware.append(middleware)
        return self

    def add_router(self, path, router):
        """
        Adds a router to the list of routers
        @type path: str
        @type router: growler.Router
        """
        self.routers.append(router)

    def _find_route(self, method, path):
        """
        Internal function for finding a route which matches the path
        """
        found = None
        for r in self.patterns:
            print('r', r)
            if r[1] == path:
                print("path matches!!!")
                # self.route_to_use.set_result(r(2))
                # return
                found = r[2]
                print("found:: ", found)
                break
        self.route_to_use.set_result(found)
        print("_find_route done ({})".format(found))
        if found is None:
            raise HTTPErrorNotFound()
        # return self.route_to_use
        # yield from asyncio.sleep(1)
        # yield

    def _middleware_boot(self, req, res, next):
        """The initial middleware"""
        pass

    def print_router_tree(self):
        for r in self.routers:
            r.print_tree()

    #
    # Configuration functions
    #

    def enable(self, name):
        """Set setting 'name' to true"""
        self.config[name] = True

    def disable(self, name):
        """Set setting 'name' to false"""
        self.config[name] = False

    def enabled(self, name):
        """
        Returns whether a setting has been enabled. This just casts the
        configuration value to a boolean.
        """
        return bool(self.config[name])

    def require(self, future):
        """
        Will wait for the future before beginning to serve web pages. Useful
        for things like database connections.
        """
        self._wait_for.append(future)

    #
    # dict-like access for application configuration options
    #

    def __setitem__(self, key, value):
        """Sets a member of the application's configuration."""
        self.config[key] = value

    def __getitem__(self, key):
        """Gets a member of the application's configuration."""
        return self.config[key]

    def __delitem__(self, key):
        del self.config[key]

    #
    # Helper Functions for easy server creation
    #

    def create_server(self, **server_config):
        """
        Helper function which constructs a listening server, using the default
        growler.http.protocol.Protocol which responds to this app.

        This function exists only to remove boilerplate code for starting up a
        growler app.

        @param server_config: These keyword arguments parameters are passed
            directly to the BaseEventLoop.create_server function. Consult their
            documentation for details.
        @returns asyncio.coroutine which should be run inside a call to
            loop.run_until_complete()
        """
        return self.loop.create_server(
            Growler.HTTPProtocol.get_factory(self, self.loop),
            **server_config
        )

    def create_server_and_run_forever(self, **server_config):
        """
        Helper function which constructs an HTTP server and listens the loop
        forever.

        This function exists only to remove boilerplate code for starting up a
        growler app.

        @param server_config: These keyword arguments parameters are passed
            directly to the BaseEventLoop.create_server function. Consult their
            documentation for details.
        """
        self.loop.run_until_complete(self.create_server(**server_config))
        self.loop.run_forever()
