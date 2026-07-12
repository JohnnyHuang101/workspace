what eahc file does, later make it into langraph ndoes


1) apis.py <-- this is the entry point of the pipeline, extracts inital data and images from URL and set sup model

2) exploration <-- generates a executoin plan

3) executor <-- for each step its going to spawn either 1 or many woekrs to get the search DONE

4) report <-- generates the final opinion and sends to user 



\\\\\


new idea:

1) import suggestions before file run --> fixes nasty refactors and having to move imports after restructuring files


states

1) sandbox enviornment setup based on user's app. 1) brief loop to constructure file system metastructure. 

2) setup docker enviornment and verify that the app can indeed be launched.


3) sandboxing to see erros potentially --> if so then  React lopo to dymically debug error a) tools: scan for functions/class b) add import at top of file c) try run file


4) no duplication of imports 


5) sandbox validation that th4e script succesfully compiles (should be addressed in before state) but also that the app launches
