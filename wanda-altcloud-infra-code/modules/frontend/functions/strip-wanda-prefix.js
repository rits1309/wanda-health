// Strips the /wanda prefix before forwarding to the ALB origin. The
// frontend's own API client code calls <base-url>/wanda/v1/..., but the
// backend's routes (and the load balancer's own path-based routing rules)
// only know about /v1/... -- this function bridges the two so neither the
// frontend's code nor the backend's routing has to know about the other's
// naming.
function handler(event) {
    var request = event.request;
    var uri = request.uri;

    if (uri === '/wanda' || uri.indexOf('/wanda/') === 0) {
        request.uri = uri.substring(6) || '/';
    }

    return request;
}
