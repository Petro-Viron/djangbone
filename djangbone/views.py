import datetime
import json

from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponse, Http404
from django.views.generic import View
from django.forms.models import model_to_dict

class DjangboneJSONEncoder(json.JSONEncoder):
    """
    JSON encoder that converts additional Python types to JSON.

    Currently only datetime.datetime instances are supported.
    """
    def default(self, obj):
        """
        Convert datetime objects to ISO-compatible strings during json serialization.
        """
        return obj.isoformat() if isinstance(obj, datetime.datetime) else str(obj)


class BackboneAPIView(View):
    """
    Abstract class view, which makes it easy for subclasses to talk to backbone.js.

    Supported operations (copied from backbone.js docs):
        create -> POST   /collection
        read ->   GET    /collection[/id]
        update -> PUT    /collection/id
        delete -> DELETE /collection/id
    """
    base_queryset = None        # Queryset to use for all data accesses, eg. User.objects.all()
    serialize_fields = None     # Tuple of field names that should appear in json output

    # Optional pagination settings:
    page_size = None            # Set to an integer to enable GET pagination (at the specified page size)
    page_param_name = 'p'       # HTTP GET parameter to use for accessing pages (eg. /widgets?p=2)

    # Override these attributes with ModelForm instances to support PUT and POST requests:
    add_form_class = None       # Form class to be used for POST requests
    edit_form_class = None      # Form class to be used for PUT requests

    # Override these if you have custom JSON encoding/decoding needs:
    json_encoder = DjangboneJSONEncoder()
    json_decoder = json.JSONDecoder()

    def dispatch(self, request, *args, **kwargs):
        # changing method here isn't working, doing it directly in POST
        # request.method = request.META.get('HTTP_X_HTTP_METHOD_OVERRIDE', request.method)

        # copied / modified from View.dispatch....

        # adding header does not work when there is a form submission
        # using a hidden iframe (i.e. for file uploads)....
        # request_method = request.META.get('HTTP_X_HTTP_METHOD_OVERRIDE', request.method)
        # if request_method.lower() in self.http_method_names:
        #     handler = getattr(self, request_method.lower(), self.http_method_not_allowed)
        # else:
        #     handler = self.http_method_not_allowed

        request_method = request.method.lower()
        if request_method == 'post':
            request_method = request.POST.get('_method', 'post').lower()
        if request_method in self.http_method_names:
            handler = getattr(self, request_method, self.http_method_not_allowed)
        else:
            handler = self.http_method_not_allowed
        self.request = request
        self.args = args
        self.kwargs = kwargs
        return handler(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        """
        Handle GET requests, either for a single resource or a collection.
        """
        if kwargs.get('id'):
            return self.get_single_item(request, *args, **kwargs)
        else:
            return self.get_collection(request, *args, **kwargs)

    def get_single_item(self, request, *args, **kwargs):
        """
        Handle a GET request for a single model instance.
        """
        try:
            qs = self.base_queryset.filter(id=kwargs['id'])
            assert len(qs) == 1
        except AssertionError:
            raise Http404
        output = self.serialize_qs(qs)
        return self.success_response(output)

    def get_collection(self, request, *args, **kwargs):
        """
        Handle a GET request for a full collection (when no id was provided).
        """
        qs = self.base_queryset
        output = self.serialize_qs(qs)
        return self.success_response(output)

    def get_request_data(self, request):
        format = request.META.get('CONTENT_TYPE', 'application/json')
        if format.find("application/x-www-form-urlencoded") != -1:
            return (request.POST, None)
        elif format.find("multipart/form-data") != -1:
            return (request.POST, request.FILES)
        else: # fallback to json
            request_dict = self.json_decoder.decode(request.raw_post_data)
            return (request_dict, None)

    def post(self, request, *args, **kwargs):
        """
        Handle a POST request by adding a new model instance.

        This view will only do something if BackboneAPIView.add_form_class is specified
        by the subclass. This should be a ModelForm corresponding to the model used by
        base_queryset.

        Backbone.js will send the new object's attributes as json in the request body,
        so use our json decoder on it, rather than looking at request.POST.
        """
        if self.add_form_class == None:
            return HttpResponse('POST not supported', status=405)
        try:
            request_dict, request_files  = self.get_request_data(request)
        except ValueError:
            return HttpResponse('Invalid POST DATA', status=400)
        form = self.add_form_class(request_dict, request_files)
        if hasattr(form, 'set_request'):
            form.set_request(request)
        if form.is_valid():
            new_object = form.save()
            # Serialize the new object to json using our built-in methods.
            # The extra DB read here is not ideal, but it keeps the code DRY:
            wrapper_qs = self.base_queryset.filter(id=new_object.id)
            return self.success_response(self.serialize_qs(wrapper_qs, single_object=True))
        else:
            return self.validation_error_response(form.errors)

    def put(self, request, *args, **kwargs):
        """
        Handle a PUT request by editing an existing model.

        This view will only do something if BackboneAPIView.edit_form_class is specified
        by the subclass. This should be a ModelForm corresponding to the model used by
        base_queryset.
        """
        if self.edit_form_class == None or not kwargs.has_key('id'):
            return HttpResponse('PUT not supported', status=405)
        try:
            request_dict, request_files  = self.get_request_data(request)
            instance = self.base_queryset.get(id=kwargs['id'])
        except ValueError:
            return HttpResponse('Invalid PUT DATA', status=400)
        except ObjectDoesNotExist:
            raise Http404
        form = self.edit_form_class(request_dict, request_files, instance=instance)
        if hasattr(form, 'set_request'):
            form.set_request(request)
        if form.is_valid():
            item = form.save()
            wrapper_qs = self.base_queryset.filter(id=item.id)
            return self.success_response(self.serialize_qs(wrapper_qs, single_object=True))
        else:
            return self.validation_error_response(form.errors)

    def delete(self, request, *args, **kwargs):
        """
        Respond to DELETE requests by deleting the model and returning its JSON representation.
        """
        if not kwargs.has_key('id'):
            return HttpResponse('DELETE is not supported for collections', status=405)
        qs = self.base_queryset.filter(id=kwargs['id'])
        if qs:
            output = self.serialize_qs(qs)
            qs.delete()
            return self.success_response(output)
        else:
            raise Http404

    def serialize_qs(self, queryset, single_object=False):
        """
        Serialize a queryset into a JSON object that can be consumed by backbone.js.

        If the single_object argument is True, or the url specified an id, return a
        single JSON object, otherwise return a JSON array of objects.
        """
        values = queryset.values(*self.serialize_fields) if self.serialize_fields else queryset.values()
        if single_object or self.kwargs.get('id'):
            # For single-item requests, convert ValuesQueryset to a dict simply
            # by slicing the first item:
            json_output = self.json_encoder.encode(values[0] if len(values) else [])
        else:
            # Process pagination options if they are enabled:
            if isinstance(self.page_size, int):
                try:
                    page_number = int(self.request.GET.get(self.page_param_name, 1))
                    offset = (page_number - 1) * self.page_size
                except ValueError:
                    offset = 0
                values = values[offset:offset+self.page_size]
            json_output = self.json_encoder.encode(list(values))
        return json_output

    def success_response(self, output):
        """
        Convert json output to an HttpResponse object, with the correct mimetype.
        """
        return HttpResponse(output, mimetype='application/json')

    def validation_error_response(self, form_errors):
        """
        Return an HttpResponse indicating that input validation failed.

        The form_errors argument contains the contents of form.errors, and you
        can override this method is you want to use a specific error response format.
        By default, the output is a simple text response.
        """
        return HttpResponse('ERROR: validation failed')

class CustomBackboneAPIView(BackboneAPIView):

    def serialize_item(self, item):
        item_dict = model_to_dict(item)
        if self.serialize_fields:
            for k in item_dict.keys():
                if k not in self.serialize_fields: del item_dict[k]
        item_dict['id'] = item.pk
        return item_dict

    def validation_error_response(self, form_errors):
        errors = ", ".join([ "%s: %s" % (key, ", ".join([error for error in errors])) for key, errors in form_errors.iteritems() ])
        return HttpResponse(errors, status=500)

    def serialize_qs(self, queryset, single_object=False):
        if single_object or self.kwargs.get('id'):
            # For single-item requests, convert ValuesQueryset to a dict simply
            # by slicing the first item:
            json_output = self.json_encoder.encode(self.serialize_item(queryset[0]))
        else:
            paginated_queryset = queryset
            # Process pagination options if they are enabled:
            if isinstance(self.page_size, int):
                try:
                    page_number = int(self.request.GET.get(self.page_param_name, 1))
                    offset = (page_number - 1) * self.page_size
                except ValueError:
                    offset = 0
                paginated_queryset = queryset[offset:offset+self.page_size]
            values = [ self.serialize_item(i) for i in paginated_queryset ]
            json_output = self.json_encoder.encode(values)
        return json_output
