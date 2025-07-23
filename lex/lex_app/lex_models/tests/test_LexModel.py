# from lex.lex_app.lex_models.tests.factories.LexModel_factory import (
#     DummyLexModel,
#     LexModelFactory,
# )
#
# from lex.lex_app.lex_models.tests.models.abstract_model_test_case import (
#     AbstractModelTestCase,
#     FakeRequest,
# )
# from lex.lex_app.rest_api.context import context_id
#
#
# class TestLexModel(AbstractModelTestCase):
#     test_model = DummyLexModel
#
#     def test_initial_instantiation(self):
#         model = LexModelFactory.create()
#         self.assertEqual(model.created_by, "Initial Data Upload")
#         self.assertEqual(model.edited_by, None)
#
#     def test_edit_instance(self):
#         model = LexModelFactory.create()
#         model.dummy_field = "New Value"
#         model.save()
#         self.assertEqual(model.created_by, "Initial Data Upload")
#         self.assertEqual(model.edited_by, "Initial Data Upload")
#
#     def test_initial_instantiation_from_user(self):
#         request = FakeRequest(name="John Doe", sub="1234")
#         token = context_id.set(
#             {"context_id": "abc", "request_obj": request, "calculation_id": "xyz"}
#         )
#         model = LexModelFactory.create()
#         try:
#             self.assertEqual(model.created_by, "John Doe (1234)")
#             self.assertEqual(model.edited_by, None)
#         finally:
#             # Reset the context_id, since it is peristent between tests
#             context_id.reset(token)
#
#     def test_edit_by_user(self):
#         model = LexModelFactory.create()
#         request = FakeRequest(name="John Doe", sub="1234")
#         token = context_id.set(
#             {"context_id": "abc", "request_obj": request, "calculation_id": "xyz"}
#         )
#         try:
#             model.dummy_field = "New Value"
#             model.save()
#             self.assertEqual(model.created_by, "Initial Data Upload")
#             self.assertEqual(model.edited_by, "John Doe (1234)")
#         finally:
#             context_id.reset(token)
