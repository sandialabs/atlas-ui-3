# TODO and Ideas

Future enhancements and feature ideas for the Chat UI application.

In the markplace. 
* always refresh the memory/ clear it. 
* so if someone slected a tool, then went to the marketplace and deselcted taht server, then the selected tool is still in memory. which is worng .

---------
If a tool returns a file, then safe this into the session ... in addition tothe current bevhior of showing it to the user for download. 
-- so for example if one tool makes a .csv then in teh same session we want the csv avaible for another tool to analyze it. 


For the code execution tool logging the log file shoudl be just main_log_path = 'logs/app.log' since it will be run from the backend. 

-------------

ideas.  FUTURE


### Authorization Improvements

- Audit logging



### Session Management
- If a special `session_start` function exists, invoke it when a user first starts interacting with the server
- Inject session and user name in tool calling, similar to the file setup


### Caching
- Response caching for similar queries