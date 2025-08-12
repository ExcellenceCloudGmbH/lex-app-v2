import copy

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_api_key.permissions import HasAPIKey

from lex.lex_app.rest_api.model_collection.model_collection import ModelCollection


class ModelStructureObtainView(APIView):
    http_method_names = ["get"]
    permission_classes = [HasAPIKey | IsAuthenticated]

    # These must be set when wiring up the view:
    model_collection = None
    get_model_structure_func = None
    get_container_func = None

    def delete_restricted_nodes_from_model_structure(self, tree, user):
        """
        Recursively remove nodes the user cannot read.
        """
        for key in list(tree.keys()):
            node = tree[key]
            # if it's a leaf, check read permission
            if "children" not in node:
                perms = self.get_container_func(
                    key
                ).get_general_modification_restrictions_for_user(user)
                if not perms.get("can_read_in_general", False):
                    del tree[key]
                    continue
            # recurse into children
            if "children" in node:
                self.delete_restricted_nodes_from_model_structure(
                    node["children"], user
                )

    def get(self, request, *args, **kwargs):
        user = request.user

        # 1) copy the raw tree
        structure = copy.deepcopy(self.get_model_structure_func())
        # 2) prune unauthorized
        self.delete_restricted_nodes_from_model_structure(structure, user)

        # 3) annotate with serializers, but only on real model nodes
        def annotate(subtree):
            for node_id, node in subtree.items():
                # attempt to fetch a ModelContainer; folders will throw or return None
                try:
                    container = self.get_container_func(node_id)
                except Exception:
                    container = None

                if container and hasattr(container, "serializers_map"):
                    node["available_serializers"] = list(
                        container.serializers_map.keys()
                    )

                # always recurse into children if present
                children = node.get("children")
                if isinstance(children, dict):
                    annotate(children)

        annotate(structure)

        return Response(structure)


class ModelStylingObtainView(APIView):
    http_method_names = ["get"]
    model_collection: ModelCollection = None
    permission_classes = [HasAPIKey | IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user = request.user
        user_dependent_model_styling = self.model_collection.model_styling.copy()
        for key in user_dependent_model_styling.keys():
            # FIXME remove try-catch
            try:
                container = self.model_collection.get_container(key).model_class

                if hasattr(
                    container, "modification_restriction"
                ):  # FIXME change these ugly calls of hasattr
                    # FIXME: this is only set if there is an entry in @user_dependent_model_styling for the model
                    #   if this is not the case (which mostly holds), then the restrictions are not transfered to the
                    #   frontend --> fix this via own route for modification_restriction (which is better anyway)
                    user_dependent_model_styling[key][
                        "can_read_in_general"
                    ] = container.modification_restriction.can_read_in_general(
                        user, violations=None
                    )
                    user_dependent_model_styling[key][
                        "can_modify_in_general"
                    ] = container.modification_restriction.can_modify_in_general(
                        user, violations=None
                    )
                    user_dependent_model_styling[key][
                        "can_create_in_general"
                    ] = container.modification_restriction.can_create_in_general(
                        user, violations=None
                    )
            except KeyError:
                # happens if key not in container
                pass

        return Response(user_dependent_model_styling)


class Overview(APIView):
    http_method_names = ["get"]
    HTML_reports = None

    def get(self, request, *args, **kwargs):
        user = request.user
        class_name = kwargs["report_name"]
        html_report_class = self.HTML_reports[class_name]
        html = html_report_class().get_html(user)
        return Response(html)


class ProcessStructure(APIView):
    http_method_names = ["get"]
    processes = None

    def get(self, request, *args, **kwargs):
        class_name = kwargs["process_name"]
        process_class = self.processes[class_name]
        process_structure = process_class().get_structure()
        return Response(process_structure)
