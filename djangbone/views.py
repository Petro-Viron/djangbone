import datetime
import json

from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponse, Http404
from django.views.generic import View
from django.forms.models import model_to_dict
from utils.logging import logging_dict, get_data_diff

import logging
logger = logging.getLogger('pivot.api')

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
    request_type = "json"

    # Override these if you have custom JSON encoding/decoding needs:
    json_encoder = DjangboneJSONEncoder()
    json_decoder = json.JSONDecoder()

    def dispatch(self, request, *args, **kwargs):
        """
        Allow emulating all http methods over POST with an _method field
        to specify the actual method.
        i.e. _method = 'PUT' will call the put method.

        This can also be done with including a custom header on the request.
        However, this fails with something like jquery form plugin which uses
        a hidden iframe to upload files.

        """
        request_method = request.method.lower()
        if request_method == 'post':
            request_method = request.POST.get('_method', 'post').lower()
        if request_method in self.http_method_names:
            handler = getattr(self, "_" + request_method, self.http_method_not_allowed)
        else:
            handler = self.http_method_not_allowed
        self.request = request
        self.args = args
        self.kwargs = kwargs
        return handler(request, *args, **kwargs)

    def create(self, data={}, files={}):
        """
        Data, files will be a key, value pair. Child class is responsible
        for saving the data.

        Return values:
        True, dict() -> success (dict is created object)
        False, { 'status': http_status, 'errors': errors to be returned } 

        """
        return False, { 'status': 501 }

    def read(self, id=None):
        """
        Return single item or collection as dict
        Return none for 404
        """
        if id:
            return self.read_single_item(id)
        else:
            return self.read_collection()

    def read_single_item(self, id):
        return None

    def read_collection(self):
        return None

    def update(self, id, data={}, files={}):
        """
        Data, files will be a key, value pair. Child class is responsible
        for saving the data.

        Return values:
        True, dict() -> success (dict is updated object)
        False, { 'status': http_status, 'errors': errors to be returned } 

        """
        return False, { 'status': 501 }

    def delete(self, id):
        """
        Return True on succesful delete, false otherwise
        """
        return False

    def success_response(self, data=None):
        if data: obj = self.json_encoder.encode(data)
        else: obj = ""
        if self.request_type == "form-multipart":
            mimetype='text/plain'
        else:
            mimetype='application/json'

        return HttpResponse(obj, mimetype=mimetype)

    def error_response(self, data=None, status=400):
        if data: errors = self.json_encoder.encode({"error":data})
        else: errors = ""
        if self.request_type == "form-multipart":
            errors = "<textarea status='%d'>%s</textarea>"%(status,errors)
            # if we return the errors with a status of 500,
            # firefox puts the response inside a "pre" element...
            # so we need to respond with a 200 code and deal with the error on the client
            return HttpResponse(errors, mimetype="text/html")
        else:
            return HttpResponse(errors, status=status, mimetype="application/json")

    def get_request_data(self, request):
        format = request.META.get('CONTENT_TYPE', 'application/json')
        if format.find("application/x-www-form-urlencoded") != -1:
            self.request_type = "form"
            return (request.POST, None)
        elif format.find("multipart/form-data") != -1:
            self.request_type = "form-multipart"
            return (request.POST, request.FILES)
        else: # fallback to json
            self.request_type = "json"
            request_dict = self.json_decoder.decode(request.raw_post_data)
            return (request_dict, None)

    def _get(self, request, *args, **kwargs):
        """
        Handle GET requests, either for a single resource or a collection.
        """
        data = self.read(kwargs.get('id', None))
        if data:
            return self.success_response(data)
        else:
            return self.error_response(status=404)

    def _post(self, request, *args, **kwargs):
        try:
            data, files = self.get_request_data(request)
        except ValueError:
            return self.error_response(status=400)
        success, data = self.create(data, files)

        if success:
            return self.success_response(data)
        else:
            return self.error_response(data.get('errors', {}), data.get('status', 400))

    def _put(self, request, *args, **kwargs):
        try:
            id = kwargs['id']
            data, files = self.get_request_data(request)
        except ValueError:
            return HttpResponse('Invalid POST DATA', status=400)
        except KeyError:
            raise Http404
        success, data = self.update(id, data, files)

        if success:
            return self.success_response(data)
        else:
            return self.error_response(data.get('errors', {}), data.get('status', 400))

    def _delete(self, request, *args, **kwargs):
        if not kwargs.has_key('id'):
            return HttpResponse('DELETE is not supported for collections', status=405)
        id = kwargs['id']
        success = self.delete(id)
        if success:
            return self.success_response()
        else:
            return self.error_response(status=404)

class ModelAPIView(BackboneAPIView):
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

    def user_has_perm(self, request, obj, action=None):
        return True

    def serialize_qs(self, queryset, single_object=False):
        """
        Serialize a queryset into a JSON object that can be consumed by backbone.js.

        If the single_object argument is True, or the url specified an id, return a
        single JSON object, otherwise return a JSON array of objects.
        """
        values = queryset.values(*self.serialize_fields) if self.serialize_fields else queryset.values()
        if self.kwargs.get('id') or single_object:
            # For single-item requests, convert ValuesQueryset to a dict simply
            # by slicing the first item:
           return (values[0] if len(values) else {})
        else:
            # Process pagination options if they are enabled:
            if isinstance(self.page_size, int):
                try:
                    page_number = int(self.request.GET.get(self.page_param_name, 1))
                    offset = (page_number - 1) * self.page_size
                except ValueError:
                    offset = 0
                values = values[offset:offset+self.page_size]
            return list(values)

    def read_single_item(self, id):
        """
        Handle a GET request for a single model instance.
        """
        try:
            qs = self.base_queryset.filter(id=id)
            assert len(qs) == 1
        except AssertionError:
            return None
        if not self.user_has_perm(self.request, qs[0], 'read_single_item'):
            return None
        return self.serialize_qs(qs)

    def read_collection(self):
        """
        Handle a GET request for a full collection (when no id was provided).
        """
        qs = self.base_queryset
        if not self.user_has_perm(self.request, qs[0], 'read_collection'):
            return None
        return self.serialize_qs(qs)

    def create(self, data={}, files={}):
        """
        Handle a POST request by adding a new model instance.

        This view will only do something if BackboneAPIView.add_form_class is specified
        by the subclass. This should be a ModelForm corresponding to the model used by
        base_queryset.

        Backbone.js will send the new object's attributes as json in the request body,
        so use our json decoder on it, rather than looking at request.POST.
        """
        if self.add_form_class == None:
            return False, { 'status': 501 }
        if not self.user_has_perm(self.request, None, 'create'):
            return False, { 'status': 403 }
        form = self.add_form_class(data, files)
        if hasattr(form, 'set_request'):
            form.set_request(self.request)
        if form.is_valid():
            new_object = form.save()
            logger.info("%s:CREATE:SUCCESS:%s: id=%s, data=%s, files=%s"%\
                    (self.base_queryset.model.__name__, self.request.user.username, new_object.pk, logging_dict(data), logging_dict(files)))
            # Serialize the new object to json using our built-in methods.
            # The extra DB read here is not ideal, but it keeps the code DRY:
            wrapper_qs = self.base_queryset.filter(id=new_object.id)
            return True, self.serialize_qs(wrapper_qs, single_object=True)
        else:
            logger.warning("%s:CREATE:ERROR:%s: data=%s, files=%s, errors=%s"%\
                    (self.base_queryset.model.__name__, self.request.user.username, logging_dict(data), logging_dict(files), logging_dict(form.errors)))
            return False, { 'errors': form.errors, 'status': 400 }

    def update(self, id, data={}, files={}):
        """
        Handle a PUT request by editing an existing model.

        This view will only do something if BackboneAPIView.edit_form_class is specified
        by the subclass. This should be a ModelForm corresponding to the model used by
        base_queryset.
        """
        if self.edit_form_class == None:
            return False, { 'status': 501 }
        qs = self.base_queryset.filter(pk=id)
        if not len(qs) == 1:
            return False, { 'status': 404 }
        instance = qs[0]
        if not self.user_has_perm(self.request, instance, 'update'):
            return False, { 'status': 404 }
        form = self.edit_form_class(data, files, instance=instance)
        if hasattr(form, 'set_request'):
            form.set_request(self.request)
        data_diff = get_data_diff(qs, data)
        if form.is_valid():
            logger.info("%s:UPDATE:SUCCESS:%s: id=%s, updated_data=%s, files=%s"%\
                    (self.base_queryset.model.__name__, self.request.user.username, instance.pk, data_diff, logging_dict(files)))
            item = form.save()
            wrapper_qs = self.base_queryset.filter(id=item.id)
            return True, self.serialize_qs(wrapper_qs, single_object=True)
        else:
            logger.info("%s:UPDATE:ERROR:%s: id=%s, updated_data=%s, files=%s"%\
                    (self.base_queryset.model.__name__, self.request.user.username, instance.pk, data_diff, logging_dict(files)))
            return False, { 'errors': form.errors, 'status': 400 }

    def delete(self, id):
        """
        Respond to DELETE requests by deleting the model
        """
        qs = self.base_queryset.filter(id=id)
        if qs:
            if not self.user_has_perm(self.request, qs[0], 'delete'):
                return False
            logger.info("%s:DELETE:SUCCESS:%s: id=%s"%\
                    (self.base_queryset.model.__name__, self.request.user.username, qs[0].pk))
            qs.delete()
            return True
        else:
            return False

class CustomModelAPIView(ModelAPIView):

    def serialize_item(self, item):
        item_dict = model_to_dict(item)
        if self.serialize_fields:
            for k in item_dict.keys():
                if k not in self.serialize_fields: del item_dict[k]
        item_dict['id'] = item.pk
        return item_dict

    def serialize_qs(self, queryset, single_object=False):
        if single_object or self.kwargs.get('id'):
            # For single-item requests, convert ValuesQueryset to a dict simply
            # by slicing the first item:
            return self.serialize_item(queryset[0])
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
            return values

