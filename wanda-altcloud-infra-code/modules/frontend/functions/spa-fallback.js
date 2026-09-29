// Rewrites client-side route paths to /index.html *before* the origin is
// asked, so react-router's routes survive a hard refresh -- without relying
// on CloudFront's distribution-wide custom_error_response (403/404 ->
// index.html), which would also intercept real errors from the API
// behavior (/wanda/*) and mask them as a fake 200 HTML page. This function
// is only associated with the default (S3) behavior, so it can never touch
// API traffic regardless of what this rewrite does.
//
// A request is treated as a real asset (left untouched) if its last path
// segment has a file extension (e.g. /assets/index-abc123.js, /favicon.ico);
// anything else (/, /login, /members/123) is assumed to be a client-side
// route and rewritten to /index.html.
function handler(event) {
    var request = event.request;
    var uri = request.uri;
    var lastSegment = uri.split('/').pop();

    if (lastSegment.indexOf('.') === -1) {
        request.uri = '/index.html';
    }

    return request;
}
